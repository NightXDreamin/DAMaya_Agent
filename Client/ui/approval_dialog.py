from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ApprovalDialog(QDialog):
    def __init__(self, tool_name: str, preview: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("高危操作确认")
        self.resize(640, 420)

        title = QLabel(f"工具：{tool_name}", self)
        content = QTextEdit(self)
        content.setReadOnly(True)
        content.setPlainText(preview)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("批准")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("拒绝")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(content, 1)
        layout.addWidget(buttons)
