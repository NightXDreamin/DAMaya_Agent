from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ChatWidget(QWidget):
    send_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._building_assistant = False

        self.chat_view = QTextEdit(self)
        self.chat_view.setReadOnly(True)

        self.input_box = QTextEdit(self)
        self.input_box.setPlaceholderText("输入 Maya 操作需求，Enter 发送，Shift+Enter 换行")
        self.input_box.setFixedHeight(84)

        self.send_btn = QPushButton("发送", self)
        self.send_btn.clicked.connect(self._on_send_clicked)

        bottom = QHBoxLayout()
        bottom.addWidget(self.input_box, 1)
        bottom.addWidget(self.send_btn)

        root = QVBoxLayout(self)
        root.addWidget(self.chat_view, 1)
        root.addLayout(bottom)

    def _on_send_clicked(self) -> None:
        text = self.input_box.toPlainText().strip()
        if not text:
            return
        self.input_box.clear()
        self.append_user_message(text)
        self.send_requested.emit(text)

    def append_user_message(self, text: str) -> None:
        self._building_assistant = False
        self.chat_view.append(f"\n[User]\n{text}\n")

    def start_assistant_message(self) -> None:
        self._building_assistant = True
        self.chat_view.append("\n[Assistant]\n")

    def append_assistant_chunk(self, text: str) -> None:
        if not self._building_assistant:
            self.start_assistant_message()
        cursor = self.chat_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.chat_view.setTextCursor(cursor)
        self.chat_view.ensureCursorVisible()

    def append_system_line(self, text: str) -> None:
        self._building_assistant = False
        self.chat_view.append(f"\n[System] {text}\n")
