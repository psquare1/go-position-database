from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QMessageBox as QtMessageBox,
)


class SilentMessageBox(QDialog):
    """A QMessageBox-compatible app dialog that never invokes the system alert sound."""

    Ok = QtMessageBox.Ok
    Yes = QtMessageBox.Yes
    No = QtMessageBox.No
    Cancel = QtMessageBox.Cancel
    Information = QtMessageBox.Information
    Warning = QtMessageBox.Warning
    Critical = QtMessageBox.Critical
    Question = QtMessageBox.Question

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._icon = self.Information
        self._text = ""
        self._details = ""
        self._buttons = self.Ok
        self._default_button = self.Ok
        self._clicked_button = self.Cancel
        self._built = False
        self.setModal(True)

    def setIcon(self, icon) -> None:
        self._icon = icon

    def setText(self, text: str) -> None:
        self._text = text

    def setDetailedText(self, text: str) -> None:
        self._details = text

    def setStandardButtons(self, buttons) -> None:
        self._buttons = buttons

    def setDefaultButton(self, button) -> None:
        self._default_button = button

    @staticmethod
    def _icon_details(icon) -> tuple[str, str]:
        if icon == SilentMessageBox.Critical:
            return "×", "#a94359"
        if icon == SilentMessageBox.Warning:
            return "!", "#a06a26"
        if icon == SilentMessageBox.Question:
            return "?", "#356f9f"
        return "i", "#356f9f"

    def _choose(self, button) -> None:
        self._clicked_button = button
        self.accept()

    def _build(self) -> None:
        if self._built:
            return
        self._built = True
        self.setMinimumWidth(410)
        layout = QVBoxLayout(self)
        content = QHBoxLayout()
        symbol, color = self._icon_details(self._icon)
        icon_label = QLabel(symbol)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedSize(38, 38)
        icon_label.setStyleSheet(
            f"font-size: 24px; font-weight: 800; color: white; background: {color}; "
            "border-radius: 19px;"
        )
        message = QLabel(self._text)
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        content.addWidget(icon_label, 0, Qt.AlignTop)
        content.addWidget(message, 1)
        layout.addLayout(content)

        if self._details:
            details = QPlainTextEdit(self._details)
            details.setReadOnly(True)
            details.setMinimumHeight(150)
            layout.addWidget(details)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        labels = (
            (self.Yes, "Yes"),
            (self.No, "No"),
            (self.Ok, "OK"),
            (self.Cancel, "Cancel"),
        )
        default_widget: QPushButton | None = None
        for code, label in labels:
            if not (self._buttons & code):
                continue
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, value=code: self._choose(value))
            button_row.addWidget(button)
            if code == self._default_button:
                default_widget = button
        layout.addLayout(button_row)
        if default_widget is not None:
            default_widget.setDefault(True)
            default_widget.setFocus()

    def reject(self) -> None:  # type: ignore[override]
        if self._buttons & self.Cancel:
            self._clicked_button = self.Cancel
        elif self._buttons & self.No:
            self._clicked_button = self.No
        else:
            self._clicked_button = self.Ok
        super().reject()

    def exec(self) -> int:  # type: ignore[override]
        self._build()
        super().exec()
        return self._clicked_button

    @classmethod
    def _show(
        cls,
        parent: QWidget | None,
        title: str,
        text: str,
        icon,
        buttons=None,
        default_button=None,
    ) -> int:
        box = cls(parent)
        box.setWindowTitle(title)
        box.setIcon(icon)
        box.setText(text)
        box.setStandardButtons(buttons if buttons is not None else cls.Ok)
        box.setDefaultButton(
            default_button if default_button is not None else (
                cls.Cancel if buttons is not None and buttons & cls.Cancel else cls.Ok
            )
        )
        return box.exec()

    @classmethod
    def information(cls, parent, title, text, buttons=None, default_button=None) -> int:
        return cls._show(parent, title, text, cls.Information, buttons, default_button)

    @classmethod
    def warning(cls, parent, title, text, buttons=None, default_button=None) -> int:
        return cls._show(parent, title, text, cls.Warning, buttons, default_button)

    @classmethod
    def critical(cls, parent, title, text, buttons=None, default_button=None) -> int:
        return cls._show(parent, title, text, cls.Critical, buttons, default_button)

    @classmethod
    def question(cls, parent, title, text, buttons=None, default_button=None) -> int:
        question_buttons = buttons if buttons is not None else cls.Yes | cls.No
        question_default = default_button if default_button is not None else cls.No
        return cls._show(
            parent, title, text, cls.Question, question_buttons, question_default
        )
