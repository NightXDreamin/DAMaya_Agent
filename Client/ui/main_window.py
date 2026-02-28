from __future__ import annotations

from PySide6.QtWidgets import QLabel, QMainWindow, QStatusBar, QWidget

from Client.ui.approval_dialog import ApprovalDialog
from Client.ui.chat_widget import ChatWidget
from Client.ui.worker_thread import AgentWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Maya AI Agent V1.0")
        self.resize(980, 680)

        self.chat = ChatWidget(self)
        self.setCentralWidget(self.chat)

        self.status = QStatusBar(self)
        self.status_label = QLabel("状态：就绪", self)
        self.status.addWidget(self.status_label)
        self.setStatusBar(self.status)

        self.worker = AgentWorker(self)

        self.chat.send_requested.connect(self._on_send_requested)
        self.worker.text_chunk.connect(self.chat.append_assistant_chunk)
        self.worker.tool_call.connect(self._on_tool_call)
        self.worker.tool_result.connect(self._on_tool_result)
        self.worker.error.connect(self._on_error)
        self.worker.completed.connect(self._on_complete)
        self.worker.approval_requested.connect(self._on_approval_requested)

    def _on_send_requested(self, text: str) -> None:
        self.status_label.setText("状态：处理中")
        self.chat.start_assistant_message()
        self.worker.submit(text)

    def _on_tool_call(self, tool_name: str, arguments: str) -> None:
        self.chat.append_system_line(f"调用工具：{tool_name} 参数：{arguments}")

    def _on_tool_result(self, tool_name: str, result: str) -> None:
        self.chat.append_system_line(f"工具结果：{tool_name} -> {result}")

    def _on_error(self, text: str) -> None:
        self.status_label.setText("状态：错误")
        self.chat.append_system_line(f"错误：{text}")

    def _on_complete(self) -> None:
        self.status_label.setText("状态：就绪")
        self.chat.append_system_line("处理完成")

    def _on_approval_requested(self, tool_name: str, preview: str) -> None:
        dialog = ApprovalDialog(tool_name, preview, self)
        approved = dialog.exec() == dialog.DialogCode.Accepted
        self.worker.resolve_approval(approved)
