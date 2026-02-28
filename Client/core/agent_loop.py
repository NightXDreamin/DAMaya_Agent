from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Optional, Protocol


class AgentState(Enum):
    IDLE = auto()
    PROCESSING = auto()
    STREAMING = auto()
    TOOL_PENDING = auto()
    AWAITING_APPROVAL = auto()
    TOOL_EXECUTING = auto()
    ERROR = auto()


class AgentCallbacks(Protocol):
    """Agent 运行期回调。

    ``on_approval_required`` 必须 **阻塞** 直到审批结果就绪，
    返回 ``True`` 表示批准执行，``False`` 表示拒绝。
    """

    def on_text_chunk(self, text: str) -> None: ...

    def on_think_chunk(self, text: str) -> None: ...

    def on_status_update(self, content: str) -> None: ...

    def on_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> None: ...

    def on_approval_required(self, tool_name: str, code_preview: str) -> bool: ...

    def on_tool_result(self, tool_name: str, result: dict[str, Any]) -> None: ...

    def on_error(self, error: str) -> None: ...

    def on_complete(self) -> None: ...


@dataclass
class ToolCallRecord:
    tool_name: str
    arguments_json: str


# ---------------------------------------------------------------------------
# ReAct XML 标签正则
# ---------------------------------------------------------------------------
# 允许标签带有空白字符或属性，如 <action tool="..."> 或 < action >
_RE_ACTION = re.compile(r"<\s*action(?:\s+[^>]*)?>\s*(\{.*?\})\s*<\s*/\s*action\s*>", re.DOTALL | re.IGNORECASE)
_RE_FINAL_ANSWER = re.compile(r"<\s*final_answer(?:\s+[^>]*)?>(.*?)<\s*/\s*final_answer\s*>", re.DOTALL | re.IGNORECASE)
_RE_THINK_OPEN = re.compile(r"<\s*(?:think|思考)(?:\s+[^>]*)?>", re.IGNORECASE)
_RE_THINK_CLOSE = re.compile(r"<\s*/\s*(?:think|思考)\s*>", re.IGNORECASE)


def _build_react_system_prompt(
    base_prompt: str,
    tool_schemas: list[dict[str, Any]],
) -> str:
    """将工具 schema 嵌入 System Prompt，强制 ReAct XML 协议。"""
    tool_descriptions: list[str] = []
    for schema in tool_schemas:
        func = schema.get("function", schema)
        name = func.get("name", "unknown")
        desc = func.get("description", "")
        params = json.dumps(func.get("parameters", {}), ensure_ascii=False, indent=2)
        tool_descriptions.append(f"### {name}\n描述: {desc}\n参数 JSON Schema:\n```json\n{params}\n```")

    tools_block = "\n\n".join(tool_descriptions)

    # 生成工具名速查表
    tool_names = [s.get("function", s).get("name", "unknown") for s in tool_schemas]
    tool_name_list = ", ".join(f'`{n}`' for n in tool_names)

    return f"""{base_prompt}

## 可用工具集（共 {len(tool_schemas)} 个）
{tools_block}

## 工具名速查表
仅以下工具名可用：{tool_name_list}
**你必须严格使用上面列出的工具名，不可自行编造或猜测工具名。**

## 【Agent 核心执行协议 - 严格遵守】
面对用户需求，你必须按照以下循环思考和行动。在每一步回复中，你必须严格使用以下 XML 标签格式输出（不要输出多余解释）：

1. **思考**：先用 `<think>你的分析与推导过程</think>` 输出推理链。
2. **执行**：若需要调用工具，在思考后紧跟输出 `<action>{{"tool": "工具名", "arguments": {{...}}}}</action>`。每次回复最多调用一个工具。
3. **最终回答**：如果任务已经彻底解决或无法继续，输出 `<final_answer>你的结构化 Markdown 回答</final_answer>`。

**规则**：
- **⚠️ 工具名必须从「工具名速查表」中精确复制，禁止使用任何未列出的工具名（如 execute_python、python、maya 等均不存在）。**
- 需要在 Maya 中执行 Python 代码时，使用 `run_custom_python` 工具，参数为 `{{"python_code": "你的代码"}}`。
- 你不会直接调用 function/tool API，所有工具调用通过 `<action>` 标签输出。
- 每次回复只能包含一个 `<action>` 或一个 `<final_answer>`，不要同时出现。
- `<action>` 里的 JSON 必须包含 `"tool"` 和 `"arguments"` 两个字段。
- 系统会将工具执行结果以 `[Observation]` 文本形式返回给你，你继续下一轮思考。
- 如果没有需要调用的工具，直接用 `<final_answer>` 给出最终结果。
"""


class AgentLoop:
    def __init__(
        self,
        llm_client: Any,
        tool_executor: Callable[[str, dict[str, Any]], dict[str, Any]],
        callbacks: AgentCallbacks,
        system_prompt: str,
        tool_schemas: Optional[list[dict[str, Any]]] = None,
        max_history_messages: int = 20,
        tool_repeat_limit: int = 3,
        max_react_rounds: int = 10,
        is_tool_dangerous: Optional[Callable[[str], bool]] = None,
    ):
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.callbacks = callbacks
        self.tool_schemas = tool_schemas or []
        self.max_history_messages = max_history_messages
        self.tool_repeat_limit = tool_repeat_limit
        self.max_react_rounds = max_react_rounds
        self._is_tool_dangerous = is_tool_dangerous
        self.state = AgentState.IDLE

        # 构建带工具 schema 的 ReAct System Prompt
        self._react_system_prompt = _build_react_system_prompt(system_prompt, self.tool_schemas)
        self._messages: list[dict[str, Any]] = [{"role": "system", "content": self._react_system_prompt}]
        self._tool_window: list[ToolCallRecord] = []

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self._messages

    def set_system_prompt(self, system_prompt: str) -> None:
        self._react_system_prompt = _build_react_system_prompt(system_prompt, self.tool_schemas)
        self._messages[0] = {"role": "system", "content": self._react_system_prompt}

    def process_user_input(self, user_text: str, injected_context: str = "") -> None:
        self.state = AgentState.PROCESSING

        content = user_text
        if injected_context.strip():
            content = f"{user_text}\n\n[Context]\n{injected_context}"

        self._messages.append({"role": "user", "content": content})
        self._trim_history()

        # ---- ReAct 循环 ----
        for round_idx in range(self.max_react_rounds):
            full_text = self._stream_once_text()

            # 1) 尝试提取 <final_answer>
            final_match = _RE_FINAL_ANSWER.search(full_text)
            if final_match:
                self._messages.append({"role": "assistant", "content": full_text})
                self.state = AgentState.IDLE
                self.callbacks.on_complete()
                return

            # 2) 尝试提取 <action>
            action_match = _RE_ACTION.search(full_text)
            if action_match:
                self._messages.append({"role": "assistant", "content": full_text})
                observation = self._execute_action(action_match.group(1))
                # 以 user 角色注入 Observation
                self._messages.append({"role": "user", "content": f"[Observation]\n{observation}"})
                continue

            # 3) 既没有 action 也没有 final_answer —— 当作最终回答
            self._messages.append({"role": "assistant", "content": full_text})
            self.state = AgentState.IDLE
            self.callbacks.on_complete()
            return

        # 超过最大轮次
        self.state = AgentState.ERROR
        self.callbacks.on_error("超过最大 ReAct 循环次数，已自动停止。")
        self.callbacks.on_complete()

    # ------------------------------------------------------------------
    # 流式接收 LLM 纯文本（不传 tools 参数）
    # ------------------------------------------------------------------
    def _stream_once_text(self) -> str:
        self.state = AgentState.STREAMING
        full_text_parts: list[str] = []
        
        # 简单状态机
        # 状态：NORMAL, THINKING, ACTION, ANSWERING (ANSWERING 和 NORMAL 类似)
        current_state = "NORMAL"
        buffer = ""

        # 正则用于检测标签
        # 增强版正则：支持空白、属性、大小写，以及中文 <思考> 标签
        re_tags = re.compile(r"<\s*/?\s*(think|思考|action|final_answer)(?:\s+[^>]*)?>", re.IGNORECASE)
        
        for event in self.llm_client.chat_stream(self._messages):
            if event.type == "text" and event.content:
                chunk = event.content
                full_text_parts.append(chunk)
                buffer += chunk

                while True:
                    match = re_tags.search(buffer)
                    if not match:
                        break
                    
                    tag = match.group()
                    start, end = match.span()
                    
                    # 标签前的内容处理
                    pre_content = buffer[:start]
                    
                    if current_state == "THINKING":
                        if pre_content:
                            self.callbacks.on_think_chunk(pre_content)
                    elif current_state == "ACTION":
                        # Action 内容静默缓冲，不发送给前端
                        pass
                    else: # NORMAL / ANSWERING
                        if pre_content:
                            self.callbacks.on_text_chunk(pre_content)

                    # 状态转换逻辑
                    tag_lower = tag.lower()
                    if "think" in tag_lower or "思考" in tag_lower:
                        if "/" in tag_lower:
                            current_state = "NORMAL"
                        else:
                            current_state = "THINKING"
                    elif "action" in tag_lower:
                        if "/" in tag_lower:
                            current_state = "NORMAL"
                        else:
                            current_state = "ACTION"
                    elif "final_answer" in tag_lower:
                        if "/" in tag_lower:
                            current_state = "NORMAL"
                        else:
                            current_state = "ANSWERING"
                    
                    # 移动 buffer 指针
                    buffer = buffer[end:]
                
                # 处理剩余 buffer (未闭合部分或普通文本)
                # 注意：如果 buffer 末尾可能是标签的一部分，应该保留等待下一块
                # 这里做一个简单处理：如果处于 THINKING 或 NORMAL，且 buffer 不像标签头，则发送
                # 如果处于 ACTION，始终不发送
                
                if current_state == "ACTION":
                    # Action 状态全缓冲
                    pass
                else:
                    # 检查 buffer 是否以 < 开头，防止切断标签
                    # 如果 buffer 很长且没有 <，则安全发送
                    if "<" in buffer:
                        # 有潜在标签，保留 buffer
                        # 优化：只保留 < 之后的部分
                        safe_idx = buffer.find("<")
                        if safe_idx > 0:
                            safe_content = buffer[:safe_idx]
                            if current_state == "THINKING":
                                self.callbacks.on_think_chunk(safe_content)
                            elif current_state != "ACTION":
                                self.callbacks.on_text_chunk(safe_content)
                            buffer = buffer[safe_idx:]
                    else:
                        if current_state == "THINKING":
                            self.callbacks.on_think_chunk(buffer)
                        elif current_state != "ACTION":
                            self.callbacks.on_text_chunk(buffer)
                        buffer = ""

        # 循环结束，检查 residual buffer
        # 通常这里 buffer 应该空了，或者是残缺的
        # 但如果是 Action 没闭合，buffer 里会有内容，会被后续 parse 步骤捕获报错
        # 如果是 Think 没闭合，我们把剩余的发出去
        if buffer and current_state != "ACTION":
             if current_state == "THINKING":
                 self.callbacks.on_think_chunk(buffer)
             else:
                 self.callbacks.on_text_chunk(buffer)

        return "".join(full_text_parts)

    # ------------------------------------------------------------------
    # 解析并执行 <action> JSON
    # ------------------------------------------------------------------
    def _execute_action(self, action_json: str) -> str:
        try:
            parsed = json.loads(action_json)
        except json.JSONDecodeError:
            err_msg = f"解析失败：你的 <action> JSON 格式错误，请检查并重试。原文: {action_json[:200]}"
            self.callbacks.on_error(err_msg)
            return f"[Error] {err_msg}"

        tool_name = str(parsed.get("tool", "")).strip()
        arguments = parsed.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}

        if not tool_name:
            err_msg = "解析失败：<action> 缺少 tool 字段。"
            self.callbacks.on_error(err_msg)
            return f"[Error] {err_msg}"

        self.callbacks.on_tool_call(tool_name, arguments)

        # 重复检测
        arguments_json = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        if self._is_repeated(tool_name, arguments_json):
            self.state = AgentState.ERROR
            err_msg = f"检测到重复工具调用阻断：{tool_name}"
            self.callbacks.on_error(err_msg)
            return f"[Error] {err_msg}"

        # 高危审批
        if self._is_dangerous_tool(tool_name):
            self.state = AgentState.AWAITING_APPROVAL
            preview = json.dumps(arguments, ensure_ascii=False, indent=2)
            approved = self.callbacks.on_approval_required(tool_name, preview)
            if not approved:
                return "[Error] 用户拒绝执行高危操作。"

        # 执行工具
        self.state = AgentState.TOOL_EXECUTING
        self.callbacks.on_status_update(f"正在执行 Maya 操作: {tool_name}...")
        try:
            result = self.tool_executor(tool_name, arguments)
        except KeyError:
            err_msg = f"未知工具: {tool_name}，请检查工具名是否正确。"
            self.callbacks.on_error(err_msg)
            return f"[Error] {err_msg}"
        except Exception as exc:
            err_msg = f"工具执行异常: {exc}"
            self.callbacks.on_error(err_msg)
            return f"[Error] {err_msg}"

        self.callbacks.on_tool_result(tool_name, result)

        # 构建 Observation 文本
        return json.dumps(result, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _is_repeated(self, tool_name: str, arguments_json: str) -> bool:
        self._tool_window.append(ToolCallRecord(tool_name=tool_name, arguments_json=arguments_json))
        if len(self._tool_window) > self.tool_repeat_limit:
            self._tool_window.pop(0)
        if len(self._tool_window) < self.tool_repeat_limit:
            return False
        head = self._tool_window[0]
        return all(
            i.tool_name == head.tool_name and i.arguments_json == head.arguments_json
            for i in self._tool_window
        )

    def _is_dangerous_tool(self, tool_name: str) -> bool:
        if self._is_tool_dangerous is None:
            return False
        try:
            return bool(self._is_tool_dangerous(tool_name))
        except Exception:
            return False

    def _trim_history(self) -> None:
        if len(self._messages) <= self.max_history_messages + 1:
            return
        system_msg = self._messages[0]
        tail = self._messages[-self.max_history_messages:]
        self._messages = [system_msg, *tail]
