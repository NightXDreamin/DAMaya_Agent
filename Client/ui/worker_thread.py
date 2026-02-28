from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QMutex, QObject, QThread, QWaitCondition, Signal

from Client.config import config
from Client.core.agent_loop import AgentLoop
from Client.core.llm_client import LLMClient
from Client.core.rag import DualTrackRAG
from Client.maya_host.client import MayaSocketClient
from Client.tools.maya_tools import (
    CreateAndConnectNodeTool,
    QuerySelectionContextTool,
    RunCustomPythonTool,
)
from Client.tools.registry import ToolRegistry


class AgentWorker(QThread):
    text_chunk = Signal(str)
    tool_call = Signal(str, str)
    tool_result = Signal(str, str)
    error = Signal(str)
    completed = Signal()
    approval_requested = Signal(str, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._pending_input = ""
        self._approval_mutex = QMutex()
        self._approval_wait = QWaitCondition()
        self._approval_value: bool | None = None

        self.maya_client = MayaSocketClient(
            host=config.maya_host,
            port=config.maya_port,
            timeout=config.maya_socket_timeout,
        )
        self.registry = ToolRegistry()
        self.registry.register(QuerySelectionContextTool(self.maya_client))
        self.registry.register(RunCustomPythonTool(self.maya_client))
        self.registry.register(CreateAndConnectNodeTool(self.maya_client))

        self.llm_client = LLMClient(
            api_key=config.dashscope_api_key,
            base_url=config.dashscope_base_url,
            chat_model=config.dashscope_chat_model,
        )

        docs_path = Path(__file__).resolve().parents[1] / "data" / "maya_cmds_docs.json"
        self.rag = DualTrackRAG(
            docs_path=docs_path,
            translate_api_key=config.dashscope_api_key,
            translate_base_url=config.dashscope_base_url,
            translate_model=config.dashscope_translate_model,
            selection_context_provider=lambda: self.registry.execute_tool("query_selection_context", {}),
        )

        system_prompt = "你是 Maya 技术助手。优先给出安全、可撤销的操作步骤，并在需要时调用工具。"
        self.loop = AgentLoop(
            llm_client=self.llm_client,
            tool_executor=self.registry.execute_tool,
            callbacks=self,
            system_prompt=system_prompt,
            tool_schemas=self.registry.get_all_schemas(),
            max_history_messages=config.agent_max_history_messages,
            tool_repeat_limit=config.agent_tool_repeat_limit,
            is_tool_dangerous=self.registry.is_dangerous_tool,
        )


    def submit(self, user_text: str) -> None:
        if self.isRunning():
            self.error.emit("当前仍在处理中，请稍候。")
            return
        self._pending_input = user_text
        self.start()

    def run(self) -> None:
        if not config.dashscope_api_key:
            self.error.emit("缺少 DASHSCOPE_API_KEY，请先配置 .env")
            return

        query = self._pending_input.strip()
        if not query:
            return

        context = self.rag.build_injected_context(query)
        self.loop.process_user_input(query, injected_context=context)

    def resolve_approval(self, approved: bool) -> None:
        self._approval_mutex.lock()
        self._approval_value = approved
        self._approval_wait.wakeAll()
        self._approval_mutex.unlock()

    def on_text_chunk(self, text: str) -> None:
        self.text_chunk.emit(text)

    def on_tool_call(self, tool_name: str, arguments: dict) -> None:
        self.tool_call.emit(tool_name, json.dumps(arguments, ensure_ascii=False))

    def on_approval_required(self, tool_name: str, code_preview: str) -> bool:
        self._approval_mutex.lock()
        self._approval_value = None
        self.approval_requested.emit(tool_name, code_preview)
        while self._approval_value is None:
            self._approval_wait.wait(self._approval_mutex)
        value = bool(self._approval_value)
        self._approval_mutex.unlock()
        return value

    def on_tool_result(self, tool_name: str, result: dict) -> None:
        self.tool_result.emit(tool_name, json.dumps(result, ensure_ascii=False))

    def on_error(self, error: str) -> None:
        self.error.emit(error)

    def on_complete(self) -> None:
        self.completed.emit()
