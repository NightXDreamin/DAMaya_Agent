from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Optional, Protocol

from Client.core.llm_client import LLMClient


class AgentState(Enum):
    IDLE = auto()
    PROCESSING = auto()
    STREAMING = auto()
    TOOL_PENDING = auto()
    AWAITING_APPROVAL = auto()
    TOOL_EXECUTING = auto()
    ERROR = auto()


class AgentCallbacks(Protocol):
    def on_text_chunk(self, text: str) -> None: ...

    def on_tool_call(self, tool_name: str, arguments: dict) -> None: ...

    def on_approval_required(self, tool_name: str, code_preview: str) -> bool: ...

    def on_tool_result(self, tool_name: str, result: dict) -> None: ...

    def on_error(self, error: str) -> None: ...

    def on_complete(self) -> None: ...


@dataclass
class ToolCallRecord:
    tool_name: str
    arguments_json: str


class AgentLoop:
    def __init__(
        self,
        llm_client: LLMClient,
        tool_executor: Callable[[str, dict[str, Any]], dict[str, Any]],
        callbacks: AgentCallbacks,
        system_prompt: str,
        tool_schemas: Optional[list[dict[str, Any]]] = None,
        max_history_messages: int = 20,
        tool_repeat_limit: int = 3,
        is_tool_dangerous: Optional[Callable[[str], bool]] = None,
    ):

        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.callbacks = callbacks
        self.tool_schemas = tool_schemas or []
        self.max_history_messages = max_history_messages
        self.tool_repeat_limit = tool_repeat_limit
        self._is_tool_dangerous = is_tool_dangerous
        self.state = AgentState.IDLE


        self._messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        self._tool_window: list[ToolCallRecord] = []

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self._messages

    def set_system_prompt(self, system_prompt: str) -> None:
        self._messages[0] = {"role": "system", "content": system_prompt}

    def process_user_input(self, user_text: str, injected_context: str = "") -> None:
        self.state = AgentState.PROCESSING

        content = user_text
        if injected_context.strip():
            content = f"{user_text}\n\n[Context]\n{injected_context}"

        self._messages.append({"role": "user", "content": content})
        self._trim_history()

        rounds = 0
        while rounds < 8:
            rounds += 1
            assistant_text, tool_calls = self._stream_once()

            if assistant_text:
                self._messages.append({"role": "assistant", "content": assistant_text})

            if not tool_calls:
                self.state = AgentState.IDLE
                self.callbacks.on_complete()
                return

            self._messages[-1]["tool_calls"] = tool_calls
            if self._handle_tool_calls(tool_calls):
                continue

            self.state = AgentState.IDLE
            self.callbacks.on_complete()
            return

        self.state = AgentState.ERROR
        self.callbacks.on_error("超过最大循环次数，已自动停止。")

    def _stream_once(self) -> tuple[str, list[dict[str, Any]]]:
        self.state = AgentState.STREAMING
        text_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}

        for event in self.llm_client.chat_stream(self._messages, tools=self.tool_schemas):
            if event.type == "text" and event.content:
                text_parts.append(event.content)
                self.callbacks.on_text_chunk(event.content)
            elif event.type == "tool_call_delta" and event.tool_call_delta:
                self._merge_tool_delta(tool_calls, event.tool_call_delta)

        normalized_tool_calls = [tool_calls[k] for k in sorted(tool_calls)]
        return "".join(text_parts), normalized_tool_calls

    @staticmethod
    def _merge_tool_delta(tool_calls: dict[int, dict[str, Any]], delta: dict[str, Any]) -> None:
        idx = delta.get("index", 0)
        if idx not in tool_calls:
            tool_calls[idx] = {
                "id": delta.get("id") or f"call_{idx}",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            }

        item = tool_calls[idx]
        if delta.get("id"):
            item["id"] = delta["id"]
        if delta.get("name"):
            item["function"]["name"] += delta["name"]
        if delta.get("arguments"):
            item["function"]["arguments"] += delta["arguments"]

    def _handle_tool_calls(self, tool_calls: list[dict[str, Any]]) -> bool:
        for call in tool_calls:
            self.state = AgentState.TOOL_PENDING
            function = call.get("function", {})
            tool_name = function.get("name", "")
            arguments_text = function.get("arguments", "{}")

            try:
                arguments = json.loads(arguments_text or "{}")
            except json.JSONDecodeError:
                arguments = {"raw_arguments": arguments_text}

            self.callbacks.on_tool_call(tool_name, arguments)

            if self._is_repeated(tool_name, arguments_text):
                self.state = AgentState.ERROR
                self.callbacks.on_error(f"检测到重复工具调用阻断：{tool_name}")
                self._messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(
                            {
                                "success": False,
                                "error": "重复工具调用已阻断。",
                                "tool_name": tool_name,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
                return True

            preview = json.dumps(arguments, ensure_ascii=False, indent=2)
            if self._is_dangerous_tool(tool_name):
                self.state = AgentState.AWAITING_APPROVAL
                approved = self.callbacks.on_approval_required(tool_name, preview)
                if not approved:
                    self._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(
                                {
                                    "success": False,
                                    "error": "用户拒绝执行高危操作。",
                                    "tool_name": tool_name,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                    continue

            self.state = AgentState.TOOL_EXECUTING
            result = self.tool_executor(tool_name, arguments)
            self.callbacks.on_tool_result(tool_name, result)
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        return True

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
        tail = self._messages[-self.max_history_messages :]
        self._messages = [system_msg, *tail]
