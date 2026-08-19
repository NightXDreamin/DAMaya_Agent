"""LangGraph 驱动的 Maya Agent —— 替换原有手写 ReAct XML 状态机。

核心流程:
  START → agent_node (LLM + bind_tools) → should_continue? ─(tool_calls)→ tool_node → agent_node
                                                             ─(end)→ END

v3.0 增量 (完全异步化与流式对齐):
  - 全面使用 async/await 改造图执行
  - 弃用节点级 streaming，改用 astream_events(version="v2") 全局侦听
  - 完美映射回旧的 WebSocketAgentCallbacks 事件 (on_think_chunk, on_tool_start 等)
  - 使用 AsyncSqliteSaver
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Annotated, Optional, Protocol, Sequence

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict
import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 回调协议 — 与原 agent_loop.py 的 AgentCallbacks 保持一致 (同步接口)
# ---------------------------------------------------------------------------


class AgentCallbacks(Protocol):
    """Agent 运行期回调（与原有协议兼容）。

    虽然图本身变成了 async，但我们允许回调是以同步方式实现
    （现有 WebSocketAgentCallbacks 内部做了队列发送）。
    """

    def on_text_chunk(self, text: str) -> None: ...
    def on_think_chunk(self, text: str) -> None: ...
    def on_status_update(self, content: str) -> None: ...
    def on_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> None: ...
    def on_approval_required(self, tool_name: str, code_preview: str) -> bool: ...
    def on_tool_result(self, tool_name: str, result: dict[str, Any]) -> None: ...
    def on_error(self, error: str) -> None: ...
    def on_complete(self) -> None: ...


# ---------------------------------------------------------------------------
# LangGraph State
# ---------------------------------------------------------------------------


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]


# ---------------------------------------------------------------------------
# 重复调用检测
# ---------------------------------------------------------------------------


@dataclass
class _ToolCallRecord:
    tool_name: str
    arguments_json: str


class _RepeatDetector:
    """滑动窗口检测连续相同工具调用。"""

    def __init__(self, limit: int = 3):
        self._limit = limit
        self._window: list[_ToolCallRecord] = []

    def check(self, tool_name: str, arguments: dict) -> bool:
        """返回 True 表示检测到重复。"""
        args_json = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        self._window.append(_ToolCallRecord(tool_name=tool_name, arguments_json=args_json))
        if len(self._window) > self._limit:
            self._window.pop(0)
        if len(self._window) < self._limit:
            return False
        head = self._window[0]
        return all(
            r.tool_name == head.tool_name and r.arguments_json == head.arguments_json
            for r in self._window
        )


# ---------------------------------------------------------------------------
# GraphAgent — 核心类 (Async)
# ---------------------------------------------------------------------------


class GraphAgent:
    """基于 LangGraph 的 Maya Agent (全异步)。

    Parameters
    ----------
    api_key : str
    base_url : str
    chat_model : str
    tools : list
        LangChain StructuredTool 列表。
    callbacks : AgentCallbacks
        事件推送回调。
    dangerous_tool_names : set[str]
        高危工具名称集合。
    system_prompt : str
        系统提示词。
    max_history_messages : int
        注入的历史消息上限。
    max_react_rounds : int
        最大 Agent 循环轮次（安全上限）。
    tool_repeat_limit : int
        连续相同工具调用阈值（达到后自动阻断）。
    db_path : Path | str | None
        Checkpointer SQLite 路径。None 表示使用内存。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        chat_model: str,
        tools: list,
        callbacks: AgentCallbacks,
        dangerous_tool_names: set[str],
        system_prompt: str,
        max_history_messages: int = 20,
        max_react_rounds: int = 10,
        tool_repeat_limit: int = 3,
        db_path: Path | str | None = None,
    ):
        self.callbacks = callbacks
        self.dangerous_tool_names = dangerous_tool_names
        self.system_prompt = system_prompt
        self.max_history_messages = max_history_messages
        self.max_react_rounds = max_react_rounds
        self._tools = tools
        self._repeat_detector = _RepeatDetector(limit=tool_repeat_limit)
        self._db_path = db_path

        # 构建工具名 → 工具对象的映射
        self._tool_map: dict[str, Any] = {t.name: t for t in tools}

        # 构建 LLM（绑定工具）
        # 注意：这里移除了 streaming=True，因为在 astream_events 中不需要手动 stream
        self._llm = ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model=chat_model,
            temperature=0.2,
        ).bind_tools(tools)

    async def _build_graph(self) -> Any:
        """异步构建 LangGraph StateGraph 并挂载 Checkpointer。"""

        graph = StateGraph(AgentState)
        graph.add_node("agent", self._agent_node)
        graph.add_node("tools", self._tool_node)

        graph.set_entry_point("agent")
        graph.add_conditional_edges(
            "agent",
            self._should_continue,
            {
                "tools": "tools",
                "end": END,
            },
        )
        graph.add_edge("tools", "agent")

        # Async Checkpointer
        if self._db_path is not None:
            conn_str = str(self._db_path)
            # 在 Windows 上，aio sqlite 路径可能需要特殊处理，但通常纯字符串没问题
            async_conn = await aiosqlite.connect(conn_str)
        else:
            async_conn = await aiosqlite.connect(":memory:")

        checkpointer = AsyncSqliteSaver(async_conn)
        # 我们需要保留这个连接引用以防止被 GC，也可用于后面的资源清理
        self._async_conn = async_conn

        return graph.compile(checkpointer=checkpointer)

    # ------ 节点实现 ------

    async def _agent_node(self, state: AgentState) -> dict:
        """调用 LLM。不再自己做 `.stream()`，直接 `ainvoke`。"""
        messages = list(state["messages"])
        try:
            # 直接 ainvoke，astream_events 会在外部全局捕获此过程的 Token
            response = await self._llm.ainvoke(messages)
            return {"messages": [response]}
        except Exception as exc:
            logger.exception("LLM 调用异常")
            self.callbacks.on_error(f"LLM 调用异常: {exc}")
            error_msg = AIMessage(content=f"LLM 调用失败: {exc}")
            return {"messages": [error_msg]}

    async def _tool_node(self, state: AgentState) -> dict:
        """执行工具调用。

        注意：这里的大部分执行逻辑保持不变。
        审批流程仍然因为无法轻易在此挂起而采用同步回调阻塞图的执行。
        """
        last_message = state["messages"][-1]
        if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
            return {"messages": []}

        tool_messages: list[ToolMessage] = []

        for tool_call in last_message.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            tool_call_id = tool_call["id"]

            # ---- 重复调用检测 ----
            if self._repeat_detector.check(tool_name, tool_args):
                err = f"检测到重复工具调用阻断：{tool_name}"
                self.callbacks.on_error(err)
                tool_messages.append(
                    ToolMessage(content=f"[Error] {err}", tool_call_id=tool_call_id, name=tool_name)
                )
                continue

            # ---- 高危审批 ----
            if tool_name in self.dangerous_tool_names:
                preview = json.dumps(tool_args, ensure_ascii=False, indent=2)
                # 同步阻塞等待审批
                approved = self.callbacks.on_approval_required(tool_name, preview)
                if not approved:
                    tool_messages.append(
                        ToolMessage(
                            content="[Error] 用户拒绝执行高危操作。",
                            tool_call_id=tool_call_id,
                            name=tool_name,
                        )
                    )
                    # 假抛异常给前端
                    self.callbacks.on_tool_result(tool_name, {"success": False, "error": "用户拒绝执行"})
                    continue

            # ---- 执行工具 ----
            tool = self._tool_map.get(tool_name)
            if tool is None:
                err = f"未知工具: {tool_name}"
                self.callbacks.on_error(err)
                tool_messages.append(
                    ToolMessage(content=f"[Error] {err}", tool_call_id=tool_call_id, name=tool_name)
                )
                continue

            # astream_events 不一定能完美捕获到自定义 Tool 的 on_tool_start，所以加上保险提示
            self.callbacks.on_status_update(f"正在执行 Maya 操作: {tool_name}...")
            
            try:
                # Maya 工具底层是请求 Socket，使用 ainvoke 包装同步的 invoke
                import asyncio
                result_str = await asyncio.to_thread(tool.invoke, tool_args)
            except Exception as exc:
                err = f"工具执行异常: {exc}"
                logger.exception(err)
                self.callbacks.on_error(err)
                tool_messages.append(
                    ToolMessage(content=f"[Error] {err}", tool_call_id=tool_call_id, name=tool_name)
                )
                continue

            # 解析结果用于回调
            try:
                result_dict = json.loads(result_str) if isinstance(result_str, str) else result_str
            except (json.JSONDecodeError, TypeError):
                result_dict = {"result": result_str}

            self.callbacks.on_tool_result(tool_name, result_dict)
            tool_messages.append(
                ToolMessage(content=result_str, tool_call_id=tool_call_id, name=tool_name)
            )

        return {"messages": tool_messages}

    def _should_continue(self, state: AgentState) -> str:
        """判断是否继续到工具节点。"""
        last_message = state["messages"][-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "tools"
        return "end"

    # ------ 公开运行接口 ------

    async def arun(
        self,
        session_id: str,
        user_text: str,
        history_messages: list[dict[str, Any]] | None = None,
        injected_context: str = "",
    ) -> list[dict[str, Any]]:
        """异步执行一轮完整的 Agent 对话，并全局侦听 astream_events。"""

        # 动态编译图（按需保证生命周期一致性）
        graph = await self._build_graph()

        run_config = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": self.max_react_rounds * 2 + 5,
        }

        # 检查 checkpoint
        existing_state = None
        try:
            existing_state = await graph.aget_state(run_config)
        except Exception:
            pass

        has_checkpoint = (
            existing_state is not None
            and existing_state.values
            and existing_state.values.get("messages")
        )

        if has_checkpoint:
            content = user_text
            if injected_context.strip():
                content = f"{user_text}\n\n[Context]\n{injected_context}"
            input_state: AgentState | None = {"messages": [HumanMessage(content=content)]}
        else:
            messages: list[BaseMessage] = [SystemMessage(content=self.system_prompt)]
            if history_messages:
                for msg in history_messages:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if role == "user":
                        messages.append(HumanMessage(content=content))
                    elif role == "assistant":
                        messages.append(AIMessage(content=content))

            content = user_text
            if injected_context.strip():
                content = f"{user_text}\n\n[Context]\n{injected_context}"
            messages.append(HumanMessage(content=content))
            input_state = {"messages": messages}

        logger.info(f"开始 astream_events (session: {session_id})")

        # 我们只需记录在此次调用中产生的新 assistant 消息用于返回保存
        new_assistant_message = ""

        try:
            # v2 版本的流式事件侦听
            async for event in graph.astream_events(input_state, config=run_config, version="v2"):
                kind = event["event"]
                # 过滤不关心的事件，只关注聊天模型的输出
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    
                    # 1. 思考链
                    additional = chunk.additional_kwargs or {}
                    reasoning = additional.get("reasoning_content")
                    if reasoning:
                        self.callbacks.on_think_chunk(reasoning)

                    # 2. 文本内容
                    text_content = chunk.content
                    if text_content:
                        t = text_content if isinstance(text_content, str) else str(text_content)
                        if t:
                            new_assistant_message += t
                            self.callbacks.on_text_chunk(t)

                # 我们将 tool call 的回调由 _tool_node 内部手动抛出，以配合审批的高粒度控制，
                # 所以此处无需特别侦听 on_tool_start 事件，直接依赖 _tool_node 发回即可。

        except Exception as exc:
            logger.exception("Agent 图执行异常 (astream_events)")
            self.callbacks.on_error(f"Agent 执行异常: {exc}")
        finally:
            if hasattr(self, "_async_conn") and self._async_conn:
                await self._async_conn.close()

        self.callbacks.on_complete()

        # 返回新增加的消息（非 Tool 类，仅普通对话流文本）
        # 当前架构下，如果有 Tool Call，由于 ainvoke 是整体的，astream_events 最后也会累积，
        # 简单起见，从最新 state 拿即可。
        final_state = await graph.aget_state(run_config)
        new_messages_dict: list[dict[str, Any]] = []
        
        # 为了兼容 server_web 这边的历史保存，我们挑出所有由 AI 产生的最新内容
        # 或者直接通过 state 拿到图的全量，这由外部调用方维护即可。
        # 更好的方案：既然启用了 checkpointer，外部可以其实完全不存数据库，目前暂时保留。
        
        if new_assistant_message.strip():
            new_messages_dict.append({
                "role": "assistant",
                "content": new_assistant_message
            })

        return new_messages_dict
