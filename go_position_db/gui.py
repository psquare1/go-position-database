from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, QSize, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QClipboard,
    QDesktopServices,
    QImage,
    QKeySequence,
    QPalette,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
    QTextCharFormat,
    QTextLayout,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyleOptionFrame,
    QStyledItemDelegate,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .config import DEFAULT_ROOT, load_config
from .database import GoPositionDatabase
from .dialogs import SilentMessageBox as QMessageBox
from .storage import (
    DatabaseError,
    atomic_dump_yaml,
    clean_position_files,
    formatted_score,
    iter_position_ids,
    load_position,
    position_dir,
    position_image_path,
    position_metadata_path,
    position_sgf_path,
    save_position,
)
from .sgf_viewer import ReadOnlySgfBoard, media_card_rects, render_sgf_board
from .tags import TagGraph, normalize_tag_name, validate_new_tag_name

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
SGF_FILTER = "SGF Files (*.sgf);;All Files (*)"
NEW_SGF_TEXT = "(;GM[1]FF[4]CA[UTF-8]AP[Go Position DB]SZ[19])"


@dataclass
class SearchCardOptions:
    image_size: QSize
    columns: int
    vertical: bool
    show_id: bool
    show_description: bool
    show_tags: bool
    show_metadata: bool


DISPLAY_MODES: dict[str, SearchCardOptions] = {
    "Compact": SearchCardOptions(QSize(320, 320), 4, True, False, False, False, False),
    "Standard": SearchCardOptions(QSize(320, 320), 3, True, True, False, True, False),
    "Detailed": SearchCardOptions(QSize(320, 320), 1, False, True, True, True, True),
}


def canonical_image_destination(config, position_id: str, source_suffix: str) -> Path:
    stem = Path(config.image_filename).stem
    suffix = source_suffix.lower()
    if not suffix.startswith("."):
        suffix = "." + suffix
    return position_dir(config, position_id) / f"{stem}{suffix}"


def remove_matching_files(directory: Path, extensions: tuple[str, ...]) -> None:
    extset = {ext.lower() for ext in extensions}
    for child in directory.iterdir():
        if child.is_file() and child.suffix.lower() in extset:
            child.unlink()


def suggest_next_position_id(config, prefix: str = "p", width: int = 6) -> str:
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$", re.IGNORECASE)
    max_n = 0
    for pid in iter_position_ids(config):
        m = pattern.match(pid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"{prefix}{max_n + 1:0{width}d}"


def minimal_explicit_tags(graph: TagGraph, tags: list[str]) -> list[str]:
    """Canonicalize tags and remove any tag already implied by a descendant."""
    return graph.minimal_explicit_tags(tags)


def score_chip_stylesheet(value: str, editable: bool = False) -> str:
    score = formatted_score(value)
    radius = 18 if editable else 12
    base = f"font-weight: 800; border-radius: {radius}px; padding: 3px 10px;"
    if not score:
        return "" if not value.strip() else base + " color: #7a2e3d; background: #fff1f3; border: 2px solid #d98798;"
    if score.startswith("W"):
        return base + " color: #356f9f; background: white; border: 3px solid #6f9fc4;"
    return base + " color: #8dc8f2; background: #171717; border: 3px solid #6f9fc4;"


def horizontal_rule() -> QFrame:
    rule = QFrame()
    rule.setFrameShape(QFrame.HLine)
    rule.setStyleSheet("color: #cbd8e3; background: #cbd8e3; max-height: 1px;")
    return rule


class TagQueryLineEdit(QLineEdit):
    """QLineEdit that autocompletes only the current tag token."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._tag_names: list[str] = []
        self._boolean_query_mode = False
        self._completer = QCompleter(self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setMaxVisibleItems(12)
        self._completer.setWidget(self)
        self.setTextMargins(10, 0, 10, 0)
        self._completer.activated.connect(self._insert_completion)
        self.textEdited.connect(self._on_text_edited)

    def set_boolean_query_mode(self, enabled: bool = True) -> None:
        # Kept for callers that distinguish the browse query from tag-entry fields.
        # Query editing itself deliberately remains ordinary QLineEdit editing.
        self._boolean_query_mode = enabled

    def set_tag_names(self, names: list[str]) -> None:
        self._tag_names = [normalize_tag_name(name) for name in names]
        from PySide6.QtCore import QStringListModel

        self._completer.setModel(QStringListModel(self._tag_names, self._completer))

    def focusInEvent(self, event) -> None:  # type: ignore[override]
        super().focusInEvent(event)
        self._refresh_completion_prefix()

    def _token_bounds(self) -> tuple[int, int]:
        text = self.text()
        cursor = self.cursorPosition()
        start = cursor
        end = cursor
        while start > 0 and not text[start - 1].isspace() and text[start - 1] not in "()":
            start -= 1
        while end < len(text) and not text[end].isspace() and text[end] not in "()":
            end += 1
        return start, end

    def _current_token(self) -> str:
        start, end = self._token_bounds()
        return self.text()[start:end].strip('"\'')

    def _is_existing_tag(self, token: str) -> bool:
        return token.strip('"\'').casefold() in {tag.casefold() for tag in self._tag_names}

    def _tag_matches(self, text: str) -> list[re.Match[str]]:
        token_pattern = re.compile(r'"[^"\n]*"|\'[^\'\n]*\'|[^\s()]+')
        return [
            match for match in token_pattern.finditer(text)
            if self._is_existing_tag(match.group(0))
        ]

    # Compatibility for code/tests written while completed tags were chips.
    def _chip_matches(self, text: str) -> list[re.Match[str]]:
        return self._tag_matches(text)

    def _on_text_edited(self) -> None:
        self._refresh_completion_prefix()
        self.update()

    def _refresh_completion_prefix(self) -> None:
        token = self._current_token()
        self._completer.setCompletionPrefix(token)
        if token:
            popup_rect = self.cursorRect()
            popup_rect.setWidth(max(280, self.width()))
            self._completer.complete(popup_rect)
            self._completer.popup().setMinimumWidth(max(280, self.width()))
        else:
            self._completer.popup().hide()

    def _insert_completion(self, completion: str) -> None:
        if completion.upper() in {"AND", "OR", "NOT"}:
            return
        start, end = self._token_bounds()
        text = self.text()
        insert_text = completion if " " not in completion else f'"{completion}"'
        new_text = text[:start] + insert_text + text[end:]
        self.setText(new_text)
        self.setCursorPosition(start + len(insert_text))
        self._completer.popup().hide()
        self.setFocus(Qt.TabFocusReason)
        QTimer.singleShot(0, lambda: self.setFocus(Qt.TabFocusReason))

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        super().keyPressEvent(event)
        QTimer.singleShot(0, self._refresh_completion_prefix)

    def event(self, event) -> bool:  # type: ignore[override]
        # QWidget normally consumes Tab for focus traversal before keyPressEvent.
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            completion = self._tab_completion()
            if completion:
                self._insert_completion(completion)
                event.accept()
                return True
        return super().event(event)

    def _tab_completion(self) -> str | None:
        token = self._current_token().casefold()
        if not token:
            return None
        current = self._completer.popup().currentIndex()
        if current.isValid():
            value = current.data()
            if isinstance(value, str):
                return value
        starts = [tag for tag in self._tag_names if tag.casefold().startswith(token)]
        if starts:
            return starts[0]
        contains = [tag for tag in self._tag_names if token in tag.casefold()]
        return contains[0] if contains else None

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if not self._boolean_query_mode:
            return
        text = self.text()
        if not text:
            return

        # QLineEdit cannot apply formats to substrings. Clear only its text area
        # after the native frame is painted, then draw the full query exactly once
        # with QTextLayout so tag coloring shares the same glyph geometry.
        option = QStyleOptionFrame()
        self.initStyleOption(option)
        text_rect = self.style().subElementRect(
            QStyle.SE_LineEditContents, option, self
        )
        margins = self.textMargins()
        text_rect.adjust(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setClipRect(text_rect)
        painter.fillRect(text_rect, self.palette().brush(QPalette.Base))

        metrics = self.fontMetrics()
        cursor = self.cursorPosition()
        text_offset = self.cursorRect().x() - metrics.horizontalAdvance(text[:cursor])
        if metrics.horizontalAdvance(text) <= text_rect.width():
            text_offset = text_rect.left()
        selection_start = self.selectionStart()
        selection_end = selection_start + len(self.selectedText()) if selection_start >= 0 else -1
        baseline = (self.height() - metrics.height()) // 2 + metrics.ascent()

        layout = QTextLayout(text, self.font())
        formats: list[QTextLayout.FormatRange] = []
        for match in self._tag_matches(text):
            start, end = match.span()
            tag_format = QTextCharFormat()
            tag_format.setForeground(QColor("#a34f70"))
            tag_range = QTextLayout.FormatRange()
            tag_range.start = start
            tag_range.length = end - start
            tag_range.format = tag_format
            formats.append(tag_range)
        if selection_start >= 0:
            selection_format = QTextCharFormat()
            selection_format.setBackground(self.palette().brush(QPalette.Highlight))
            selection_format.setForeground(self.palette().brush(QPalette.HighlightedText))
            selection_range = QTextLayout.FormatRange()
            selection_range.start = selection_start
            selection_range.length = selection_end - selection_start
            selection_range.format = selection_format
            formats.append(selection_range)
        layout.setFormats(formats)
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(max(text_rect.width(), metrics.horizontalAdvance(text) + 8))
        layout.endLayout()
        layout.draw(painter, QPointF(text_offset, baseline - line.ascent()))

        if self.hasFocus() and selection_start < 0 and not self.isReadOnly():
            cursor_x = text_offset + metrics.horizontalAdvance(text[:cursor])
            painter.setPen(self.palette().color(QPalette.Text))
            painter.drawLine(
                QPointF(cursor_x, baseline - metrics.ascent()),
                QPointF(cursor_x, baseline + metrics.descent()),
            )


TAG_LIST_STYLESHEET = """
QListWidget { border: 1px solid #d7cdd0; border-radius: 9px; background: palette(base); padding: 4px; }
"""


class TagChipDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):  # type: ignore[override]
        metrics = option.fontMetrics
        return QSize(metrics.horizontalAdvance(str(index.data())) + 24, 29)

    def paint(self, painter, option, index) -> None:  # type: ignore[override]
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        selected = bool(option.state & QStyle.State_Selected)
        rect = option.rect.adjusted(2, 2, -2, -2)
        painter.setPen(QPen(QColor("#c77f9b" if selected else "#dfa7ba"), 1))
        painter.setBrush(QColor("#edbfd0" if selected else "#f6dce5"))
        painter.drawRoundedRect(rect, 12, 12)
        painter.setPen(QColor("#4f2636" if selected else "#683748"))
        painter.drawText(rect, Qt.AlignCenter, str(index.data()))
        painter.restore()

def configure_tag_chip_list(widget: QListWidget, selectable: bool = True) -> None:
    widget.setFlow(QListWidget.LeftToRight)
    widget.setWrapping(True)
    widget.setResizeMode(QListWidget.Adjust)
    widget.setMovement(QListWidget.Static)
    widget.setSpacing(2)
    widget.setSelectionMode(QAbstractItemView.ExtendedSelection if selectable else QAbstractItemView.NoSelection)
    widget.setItemDelegate(TagChipDelegate(widget))
    widget.setStyleSheet(TAG_LIST_STYLESHEET)


class TagChipDisplay(QWidget):
    def __init__(self, tags: list[str], height: int = 56, centered: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.tags = tags
        self.centered = centered
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        metrics = self.fontMetrics()
        chip_height = 25
        horizontal_gap = 6
        vertical_gap = 5
        padding = 9
        available = max(1, self.width() - 4)
        rows: list[list[tuple[str, int]]] = [[]]
        row_widths = [0]
        for tag in self.tags:
            width = metrics.horizontalAdvance(tag) + padding * 2
            addition = width if not rows[-1] else width + horizontal_gap
            if rows[-1] and row_widths[-1] + addition > available:
                rows.append([])
                row_widths.append(0)
                addition = width
            rows[-1].append((tag, width))
            row_widths[-1] += addition
        total_height = len(rows) * chip_height + max(0, len(rows) - 1) * vertical_gap
        y = max(0, (self.height() - total_height) // 2)
        for row, row_width in zip(rows, row_widths):
            x = max(2, (self.width() - row_width) // 2) if self.centered else 2
            for tag, width in row:
                rect = QRect(x, y, width, chip_height)
                painter.setPen(QPen(QColor("#dfa7ba"), 1))
                painter.setBrush(QColor("#f6dce5"))
                painter.drawRoundedRect(rect, chip_height / 2, chip_height / 2)
                painter.setPen(QColor("#683748"))
                baseline = y + (chip_height + metrics.ascent() - metrics.descent()) // 2
                painter.drawText(x + padding, baseline, tag)
                x += width + horizontal_gap
            y += chip_height + vertical_gap


def tag_chip_list(tags: list[str], height: int = 56, centered: bool = False) -> TagChipDisplay:
    return TagChipDisplay(tags, height=height, centered=centered)


class MetadataKeyValueEditor(QWidget):
    """Friendly flat metadata editor that preserves structured values when supplied."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.table = QTableWidget(0, 2)
        self.table.horizontalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(150)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.add_button = QPushButton("Add field")
        self.remove_button = QPushButton("Remove selected")
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.table.itemChanged.connect(self._on_item_changed)
        self.add_button.clicked.connect(self.add_row)
        self.remove_button.clicked.connect(self.remove_selected)
        self.set_metadata({})

    def set_metadata(self, metadata: dict[str, Any]) -> None:
        self._loading = True
        self.table.setRowCount(0)
        for key, value in metadata.items():
            self._append_row(str(key), self._format_value(value))
        if not metadata:
            self._append_row("", "")
        self._loading = False

    def metadata(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for row in range(self.table.rowCount()):
            key_item = self.table.item(row, 0)
            value_item = self.table.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            value_text = value_item.text().strip() if value_item else ""
            if not key and not value_text:
                continue
            if not key:
                raise DatabaseError(f"Metadata row {row + 1} needs a descriptor.")
            if key in result:
                raise DatabaseError(f"Metadata descriptor '{key}' is duplicated.")
            result[key] = self._parse_value(value_text)
        return result

    def add_row(self) -> None:
        self._append_row("", "")
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)
        self.table.editItem(self.table.currentItem())
        self.changed.emit()

    def remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)
        if self.table.rowCount() == 0:
            self._append_row("", "")
        self.changed.emit()

    def _append_row(self, key: str, value: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(key))
        self.table.setItem(row, 1, QTableWidgetItem(value))

    def _on_item_changed(self) -> None:
        if not self._loading:
            self.changed.emit()

    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, str):
            return value
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        return yaml.safe_dump(value, default_flow_style=True, allow_unicode=True).strip()

    @staticmethod
    def _parse_value(text: str) -> Any:
        if text == "":
            return ""
        lowered = text.casefold()
        if lowered in {"true", "false", "null", "none"} or re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
            return yaml.safe_load("null" if lowered == "none" else text)
        if text.startswith(("[", "{")):
            try:
                return yaml.safe_load(text)
            except Exception as e:
                raise DatabaseError(f"Metadata value '{text}' could not be parsed: {e}")
        return text


class TagSetEditor(QWidget):
    changed = Signal()
    tag_created = Signal(str)

    def __init__(self, config=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.available_tags: list[str] = []

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.add_edit = TagQueryLineEdit()
        self.add_edit.setPlaceholderText("Type a tag and press Add or Enter")
        self.add_button = QPushButton("Add")
        row.addWidget(self.add_edit)
        row.addWidget(self.add_button)
        layout.addLayout(row)

        self.list_widget = QListWidget()
        configure_tag_chip_list(self.list_widget)
        layout.addWidget(self.list_widget)

        lower = QHBoxLayout()
        self.remove_button = QPushButton("Remove Selected")
        self.clear_button = QPushButton("Clear")
        lower.addWidget(self.remove_button)
        lower.addWidget(self.clear_button)
        layout.addLayout(lower)

        self.add_button.clicked.connect(self.add_from_edit)
        self.add_edit.returnPressed.connect(self.add_from_edit)
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.clear_tags)

    def set_available_tags(self, tags: list[str]) -> None:
        self.available_tags = [normalize_tag_name(tag) for tag in tags]
        self.add_edit.set_tag_names(self.available_tags)

    def tags(self) -> list[str]:
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]

    def set_tags(self, tags: list[str]) -> None:
        self.list_widget.clear()
        normalized = [normalize_tag_name(tag) for tag in tags]
        if self.config is not None:
            try:
                normalized = minimal_explicit_tags(TagGraph(self.config), normalized)
            except DatabaseError:
                pass
        for tag in normalized:
            self.list_widget.addItem(normalize_tag_name(tag))

    def add_from_edit(self) -> None:
        raw_text = self.add_edit.text().strip().strip('"')
        if not raw_text:
            return
        try:
            text = validate_new_tag_name(raw_text)
        except DatabaseError as e:
            QMessageBox.warning(self, "Invalid Tag", str(e))
            return
        if self.config is None:
            return
        try:
            graph = TagGraph(self.config)
            if not graph.has(text):
                if QMessageBox.question(
                    self,
                    "Create New Tag?",
                    f"'{text}' is not an existing tag. Create it and add it to this position?",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                ) != QMessageBox.Yes:
                    self.add_edit.setFocus()
                    return
                graph.add(text)
                GoPositionDatabase(self.config).rebuild_index()
                self.set_available_tags(graph.names())
                self.tag_created.emit(text)
            text = graph.canonical(text)
            current_tags = self.tags()
            implied_by = [
                existing for existing in current_tags
                if text in graph.ancestors(existing, include_self=False)
            ]
            if implied_by:
                QMessageBox.information(
                    self,
                    "Tag Already Implied",
                    f"'{text}' was not added because it is an ancestor of {', '.join(implied_by)}.",
                )
                self.add_edit.clear()
                return
            ancestors_to_remove = {
                existing for existing in current_tags
                if existing in graph.ancestors(text, include_self=False)
            }
            retained = [existing for existing in current_tags if existing not in ancestors_to_remove]
            if text not in retained:
                retained.append(text)
            self.set_tags(retained)
            self.changed.emit()
        except DatabaseError as e:
            QMessageBox.warning(self, "Tag Error", str(e))
        self.add_edit.clear()

    def remove_selected(self) -> None:
        for item in list(self.list_widget.selectedItems()):
            row = self.list_widget.row(item)
            self.list_widget.takeItem(row)
        self.changed.emit()

    def clear_tags(self) -> None:
        self.list_widget.clear()
        self.changed.emit()


class ElidedLabel(QLabel):
    """A word-wrapped label capped to a small number of lines with an ellipsis."""

    def __init__(self, text: str, max_lines: int = 6, parent: QWidget | None = None):
        super().__init__(parent)
        self.full_text = text
        self.max_lines = max_lines
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # A little slack over the line budget so the last kept line is never
        # sliced horizontally when boundingRect and the painter disagree by a pixel.
        self.setMaximumHeight(self._line_budget() + 6)
        QLabel.setText(self, text)

    def _line_budget(self) -> int:
        return self.fontMetrics().lineSpacing() * self.max_lines

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._refresh_text()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._refresh_text()

    def _refresh_text(self) -> None:
        width = max(1, self.contentsRect().width())
        metrics = self.fontMetrics()
        maximum_height = self._line_budget()
        bounds = QRect(0, 0, width, maximum_height)

        def fits(candidate: str) -> bool:
            return metrics.boundingRect(bounds, Qt.TextWordWrap, candidate).height() <= maximum_height

        if fits(self.full_text):
            QLabel.setText(self, self.full_text)
            self.setToolTip("")
            return
        low, high = 0, len(self.full_text)
        while low < high:
            middle = (low + high + 1) // 2
            if fits(self.full_text[:middle].rstrip() + "…"):
                low = middle
            else:
                high = middle - 1
        QLabel.setText(self, self.full_text[:low].rstrip() + "…")
        self.setToolTip(self.full_text)


class SearchResultCard(QFrame):
    open_requested = Signal(str)

    def __init__(self, position_id: str, record: dict[str, Any], image_path: Path | None, options: SearchCardOptions, parent: QWidget | None = None, sgf_path: Path | None = None):
        super().__init__(parent)
        self.position_id = position_id
        self.sgf_path = sgf_path
        self.sgf_start_path = list(record.get("sgf_start_path", []) or [])
        self.main_media_kind = record.get("main_media_kind", "board")
        self.setObjectName("searchResultCard")
        self.setFrameShape(QFrame.StyledPanel)
        if options.vertical:
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            details_height = 0
            if options.show_id:
                details_height += 30
            if options.show_description:
                details_height += 64
            if options.show_tags:
                details_height += 68
            if options.show_metadata:
                details_height += 110
            self.setFixedHeight(options.image_size.height() + details_height + 26)
        else:
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            self.setMinimumHeight(options.image_size.height() + 105)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18 if not options.vertical else 8, 9, 14 if not options.vertical else 10, 9)
        layout.setSpacing(7)

        if options.show_id and options.vertical:
            title = QLabel(position_id)
            title.setStyleSheet("font-size: 17px; font-weight: 700; color: #303134;")
            title.setAlignment(Qt.AlignCenter)
            title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(title)

        self.image_label = ClickableImageLabel()
        self.image_label.setFixedSize(options.image_size)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background: #f4f4f4; border: 1px solid #cccccc;")
        self.image_label.setCursor(Qt.PointingHandCursor)
        self.image_label.setToolTip("Open this position in the editor")
        self.image_label.clicked.connect(lambda: self.open_requested.emit(self.position_id))
        self.set_preview(image_path, options.image_size)

        image_column = QVBoxLayout()
        image_column.setSpacing(5)
        if not options.vertical:
            image_column.setContentsMargins(10, 0, 10, 0)
            title = QLabel(position_id)
            title.setFixedHeight(27)
            title.setAlignment(Qt.AlignCenter)
            title.setStyleSheet("font-size: 17px; font-weight: 700; color: #303134;")
            image_column.addWidget(title)
        image_column.addWidget(self.image_label, 0, Qt.AlignHCenter)
        if options.show_metadata:
            score = formatted_score(record.get("score", ""))
            if score:
                score_label = QLabel(score)
                score_label.setAlignment(Qt.AlignCenter)
                score_label.setStyleSheet(score_chip_stylesheet(score))
                score_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                image_column.addWidget(score_label, 0, Qt.AlignHCenter)
        if not options.vertical:
            image_column.addStretch(1)

        info_layout = QVBoxLayout()
        desc = (record.get("description", "") or "").strip()
        tags = [normalize_tag_name(tag) for tag in (record.get("tags", []) or [])]
        meta = record.get("metadata", {}) or {}
        if options.show_description:
            if desc:
                desc_heading = QLabel("Description")
                desc_heading.setObjectName("descriptionHeading")
                desc_heading.setFixedHeight(27)
                desc_heading.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                desc_heading.setStyleSheet(
                    "font-size: 15px; font-weight: 700; color: #426f91;"
                )
                info_layout.addWidget(desc_heading)
                desc_label = ElidedLabel(desc, max_lines=6)
                desc_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                info_layout.addWidget(desc_label)
                if options.show_tags:
                    info_layout.addWidget(horizontal_rule())

        if options.show_tags:
            if tags:
                info_layout.addWidget(tag_chip_list(tags, height=56, centered=options.vertical))
            else:
                no_tags = QLabel("No tags")
                if options.vertical:
                    no_tags.setAlignment(Qt.AlignCenter)
                info_layout.addWidget(no_tags)

        if options.show_metadata:
            if meta:
                if tags:
                    info_layout.addWidget(horizontal_rule())
                meta_frame = QFrame()
                meta_frame.setStyleSheet(
                    "QFrame { background: #f7f8f9; border: 1px solid #dfe1e5; border-radius: 7px; }"
                    "QLabel { border: none; background: transparent; }"
                )
                meta_layout = QGridLayout(meta_frame)
                meta_layout.setContentsMargins(10, 7, 10, 7)
                meta_layout.setHorizontalSpacing(12)
                meta_layout.setVerticalSpacing(5)
                for row, (descriptor, value) in enumerate(meta.items()):
                    key_label = QLabel(str(descriptor))
                    key_label.setStyleSheet("font-weight: 600; color: #5f6368;")
                    if isinstance(value, str):
                        value_text = value
                    elif value is None or isinstance(value, (bool, int, float)):
                        value_text = str(value)
                    else:
                        value_text = yaml.safe_dump(value, sort_keys=False, allow_unicode=True, default_flow_style=True).strip()
                    value_label = QLabel(value_text)
                    value_label.setWordWrap(True)
                    meta_layout.addWidget(key_label, row, 0, Qt.AlignTop)
                    meta_layout.addWidget(value_label, row, 2)
                divider = QFrame()
                divider.setFrameShape(QFrame.VLine)
                divider.setStyleSheet("color: #c8d6e3; background: #c8d6e3; max-width: 1px;")
                meta_layout.addWidget(divider, 0, 1, len(meta), 1)
                meta_layout.setColumnStretch(2, 1)
                info_layout.addWidget(meta_frame)

        if options.vertical:
            layout.addLayout(image_column)
            if options.show_tags:
                layout.addLayout(info_layout)
        else:
            body = QHBoxLayout()
            body.setSpacing(26)
            body.addLayout(image_column)
            info_layout.addStretch(1)
            body.addLayout(info_layout, 1)
            layout.addLayout(body)

    def set_preview(self, image_path: Path | None, size: QSize) -> None:
        if (
            self.main_media_kind == "board"
            and self.sgf_path is not None
            and self.sgf_path.exists()
        ):
            board = render_sgf_board(
                self.sgf_path,
                self.sgf_start_path,
                size=max(size.width(), size.height()),
            )
            if not board.isNull():
                self.image_label.setPixmap(board.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        if image_path is None or not image_path.exists():
            self.image_label.setText("No image")
            return
        pix = QPixmap(str(image_path))
        if pix.isNull():
            self.image_label.setText("Unreadable image")
            return
        self.image_label.setPixmap(pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        super().mouseDoubleClickEvent(event)
        if event.button() == Qt.LeftButton:
            self.open_requested.emit(self.position_id)


class ClickableImageLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton:
            self.clicked.emit()


class ScaledImageLabel(QLabel):
    """An image surface that continuously fits its source into the available space."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._source_pixmap = QPixmap()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(360, 360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background: #f6f2f1; border: none;")

    def set_source_pixmap(self, pixmap: QPixmap, empty_text: str = "No image") -> None:
        self._source_pixmap = pixmap
        if pixmap.isNull():
            self.setPixmap(QPixmap())
            self.setText(empty_text)
        else:
            self.setText("")
            self._fit_pixmap()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._fit_pixmap()

    def _fit_pixmap(self) -> None:
        if self._source_pixmap.isNull():
            return
        target = self.contentsRect().size()
        if target.width() > 0 and target.height() > 0:
            self.setPixmap(self._source_pixmap.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation))


class GalleryImageLabel(ScaledImageLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton:
            self.clicked.emit()


class PositionImageSurface(QWidget):
    """An editor image using exactly the same media card as the SGF board."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._source_pixmap = QPixmap()
        self._empty_text = "No image"
        self.setMinimumSize(300, 420)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_source_pixmap(self, pixmap: QPixmap, empty_text: str = "No image") -> None:
        self._source_pixmap = pixmap
        self._empty_text = empty_text
        self.update()

    def _content_rects(self) -> tuple[QRectF, QRectF, QRectF]:
        return media_card_rects(
            self.width(),
            self.height(),
            ReadOnlySgfBoard.CONTROL_PANEL_HEIGHT,
            ReadOnlySgfBoard.OUTER_PADDING,
        )

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f6f2f1"))
        outer_rect, media_rect, panel_rect = self._content_rects()
        painter.setPen(QPen(QColor("#cfc5c8"), 1))
        painter.setBrush(QColor("#fffdfd"))
        painter.drawRoundedRect(outer_rect, 8, 8)
        painter.fillRect(media_rect, QColor("#fffdfd"))
        if self._source_pixmap.isNull():
            painter.setPen(QColor("#665a5e"))
            painter.drawText(media_rect, Qt.AlignCenter, self._empty_text)
        else:
            target_size = media_rect.size().toSize()
            scaled = self._source_pixmap.scaled(
                target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            target = QRect(
                round(media_rect.center().x() - scaled.width() / 2),
                round(media_rect.center().y() - scaled.height() / 2),
                scaled.width(),
                scaled.height(),
            )
            painter.drawPixmap(target, scaled)
        painter.fillRect(panel_rect, QColor("#fffafa"))
        painter.setPen(QPen(QColor("#ddd3d6"), 1))
        painter.drawLine(panel_rect.topLeft(), panel_rect.topRight())
        painter.drawLine(panel_rect.bottomLeft(), panel_rect.bottomRight())
        painter.drawLine(panel_rect.topLeft(), panel_rect.bottomLeft())
        painter.drawLine(panel_rect.topRight(), panel_rect.bottomRight())


class PositionImageGallery(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.media_stack = QStackedWidget()
        self.media_stack.setMinimumSize(300, 420)
        self.image_surface = PositionImageSurface()
        self.image_label = self.image_surface
        self.sgf_board = ReadOnlySgfBoard()
        self.media_stack.addWidget(self.image_surface)
        self.media_stack.addWidget(self.sgf_board)
        layout.addWidget(self.media_stack, 0, 0)

        self.images: list[tuple[str, QPixmap]] = []
        self.has_sgf = False
        self.sgf_path: Path | None = None
        self.sgf_text: str | None = None
        self.board_start_paths: dict[int, list[int]] = {}
        self.selected_index = 0
        self.sgf_board.sgf_edited.connect(self._remember_sgf_text)

    def _remember_sgf_text(self, text: str) -> None:
        self.sgf_text = text

    def set_images(
        self,
        images: list[tuple[str, QPixmap]],
        sgf_path: Path | None = None,
        sgf_start_path: list[int] | tuple[int, ...] = (),
        sgf_text: str | None = None,
        board_start_paths: dict[int, list[int]] | None = None,
    ) -> None:
        self.images = images
        self.has_sgf = sgf_path is not None or sgf_text is not None
        self.sgf_path = sgf_path
        self.sgf_text = sgf_text
        self.board_start_paths = dict(board_start_paths or {})
        if self.has_sgf and board_start_paths is None:
            self.board_start_paths.setdefault(0, list(sgf_start_path))
        else:
            self.sgf_board.clear()
        self.select_image(min(self.selected_index, max(0, len(images) - 1)))

    def select_image(self, index: int) -> None:
        if not 0 <= index < len(self.images):
            return
        self.selected_index = index
        _title, pixmap = self.images[index]
        self.image_label.set_source_pixmap(pixmap, "No image")
        show_board = self.has_sgf and index in self.board_start_paths
        if show_board:
            start_path = self.board_start_paths[index]
            if self.sgf_text is not None:
                self.sgf_board.load_text(self.sgf_text, start_path)
            elif self.sgf_path is not None:
                self.sgf_board.load_file(self.sgf_path, start_path)
            self.media_stack.setCurrentWidget(self.sgf_board)
        else:
            self.media_stack.setCurrentWidget(self.image_surface)


class PositionEditor(QWidget):
    saved = Signal(str)
    database_changed = Signal()
    back_requested = Signal()
    deleted = Signal(str)

    def __init__(self, config, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.current_position_id: str | None = None
        self.pending_image_path: Path | None = None
        self.pending_image: QImage | None = None
        self.pending_sgf_path: Path | None = None
        self.pending_sgf_text: str | None = None
        self.clear_sgf_on_save = False
        self.main_description = ""
        self.main_score = ""
        self.main_media_kind = "board"
        self.main_sgf_start_path: list[int] = []
        self.solution_images: list[dict[str, Any]] = []
        self.pending_solution_sources: dict[str, Path | QImage] = {}
        self.solution_files_to_delete: set[str] = set()
        self.selected_image_index = 0
        self.transient_new_position = False
        self._loading = False
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(900)
        self.autosave_timer.timeout.connect(self.save_current)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.setAlignment(Qt.AlignVCenter)
        board_header = QWidget()
        board_header.setFixedHeight(46)
        board_header.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        board_header_layout = QHBoxLayout(board_header)
        board_header_layout.setContentsMargins(0, 0, 0, 0)
        board_header_layout.setSpacing(8)
        identity = QWidget(board_header)
        identity_layout = QHBoxLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(8)
        self.back_btn = QPushButton("Back to search")
        self.back_btn.setStyleSheet(
            "QPushButton { min-height: 38px; max-height: 38px; padding: 0 12px; "
            "background: #fffdfd; border: 1px solid #cfc5c8; border-radius: 8px; }"
            "QPushButton:hover { background: #f8e8ee; border-color: #d49aad; }"
        )
        self.back_btn.setFixedHeight(40)
        identity_layout.addWidget(self.back_btn)
        self.position_id_label = QLabel("")
        self.position_id_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        identity_layout.addWidget(self.position_id_label)
        self.header_identity = identity
        board_header_layout.addWidget(identity, 0, Qt.AlignLeft | Qt.AlignVCenter)
        board_header_layout.addStretch(1)
        self.board_header = board_header

        # Score remains part of the saved model and editor logic, but is hidden
        # until it has a node-appropriate home in the interface.
        self.score_edit = QLineEdit(self)
        self.score_edit.setFixedWidth(130)
        self.score_edit.setAlignment(Qt.AlignCenter)
        self.score_edit.hide()

        self.solution_controls = QWidget(board_header)
        self.solution_controls.setFixedHeight(46)
        solution_controls_layout = QHBoxLayout(self.solution_controls)
        solution_controls_layout.setContentsMargins(0, 3, 0, 3)
        solution_controls_layout.setSpacing(0)
        solution_controls_layout.setAlignment(Qt.AlignVCenter)
        self.baseline_menu_button = QToolButton(self.solution_controls)
        self.baseline_menu_button.setText("Set baseline")
        self.baseline_menu_button.setPopupMode(QToolButton.InstantPopup)
        self.baseline_menu_button.setStyleSheet(
            "QToolButton { min-height: 38px; max-height: 38px; "
            "border: 1px solid #a9c8dc; border-radius: 8px; "
            "background: #edf5fa; color: #356f9f; padding: 0 10px; "
            "font-weight: 650; }"
            "QToolButton:hover { background: #dcecf5; border-color: #79a9c7; }"
        )
        self.baseline_menu_button.setFixedHeight(40)
        solution_controls_layout.addWidget(self.baseline_menu_button)
        solution_controls_layout.addSpacing(10)

        self.solution_strip = QFrame(self.solution_controls)
        self.solution_strip.setObjectName("solutionStrip")
        self.solution_strip.setFixedHeight(40)
        self.solution_strip.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.solution_strip.setStyleSheet(
            "QFrame#solutionStrip { border: 1px solid #cfc5c8; border-radius: 7px; "
            "background: #fffafa; }"
        )
        self.solution_tabs_layout = QHBoxLayout(self.solution_strip)
        self.solution_tabs_layout.setContentsMargins(2, 2, 2, 2)
        self.solution_tabs_layout.setSpacing(1)
        self.solution_tabs_layout.setAlignment(Qt.AlignVCenter)
        self.solution_tab_buttons: list[QPushButton] = []
        self.solution_strip_slot = QWidget(self.solution_controls)
        self.solution_strip_slot.setFixedSize(354, 40)
        solution_strip_slot_layout = QHBoxLayout(self.solution_strip_slot)
        solution_strip_slot_layout.setContentsMargins(0, 0, 0, 0)
        solution_strip_slot_layout.setSpacing(0)
        solution_strip_slot_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        solution_strip_slot_layout.addWidget(self.solution_strip)
        solution_controls_layout.addWidget(self.solution_strip_slot)
        board_header_layout.addWidget(
            self.solution_controls, 0, Qt.AlignRight | Qt.AlignVCenter
        )

        self.sgf_menu_btn = QToolButton()
        self.sgf_menu_btn.setText("SGF")
        self.sgf_menu_btn.setPopupMode(QToolButton.InstantPopup)
        self.sgf_menu_btn.setToolTip("Choose, create, or remove the position SGF")

        self.open_folder_btn = QToolButton()
        self.open_folder_btn.setText("Folder")
        self.open_folder_btn.setToolTip("Open the position folder")
        self.delete_btn = QToolButton()
        self.delete_btn.setText("Delete")
        self.delete_btn.setToolTip("Delete this position")
        header_action_style = (
            "min-height: 38px; max-height: 38px; padding: 0 10px; "
            "border: 1px solid #cfc5c8; border-radius: 8px; "
            "background: #fffdfd; color: #4f4347; font-weight: 650;"
        )
        for button in (self.sgf_menu_btn, self.open_folder_btn, self.delete_btn):
            button.setStyleSheet(
                f"QPushButton, QToolButton {{ {header_action_style} }}"
                "QPushButton:hover, QToolButton:hover { background: #edf5fa; "
                "border-color: #a9c8dc; color: #356f9f; }"
            )
            button.setFixedSize(108, 40)
        self.delete_btn.setStyleSheet(
            "QToolButton { min-height: 38px; max-height: 38px; padding: 0 10px; "
            "border: 1px solid #bd5c70; border-radius: 8px; "
            "background: #f6c7cf; color: #702438; font-weight: 750; }"
            "QToolButton:hover { background: #ecaeb9; border-color: #a94359; "
            "color: #5f172a; }"
        )
        self.delete_btn.setFixedSize(108, 40)
        action_header = QWidget()
        action_header.setFixedHeight(46)
        action_header.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        action_header_layout = QHBoxLayout(action_header)
        action_header_layout.setContentsMargins(0, 3, 0, 3)
        action_header_layout.setSpacing(6)
        action_header_layout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        action_header_layout.addStretch(1)
        action_header_layout.addWidget(self.sgf_menu_btn)
        action_header_layout.addWidget(self.open_folder_btn)
        action_header_layout.addWidget(self.delete_btn)
        header.addWidget(board_header, 4)
        header.addWidget(action_header, 3)
        self.save_status_label = QLabel("")
        self.save_status_label.setStyleSheet("color: #5f6368;")
        layout.addLayout(header)

        self.editor_splitter = QSplitter(Qt.Horizontal)
        self.editor_splitter.setChildrenCollapsible(False)
        image_panel = QWidget()
        image_panel_layout = QVBoxLayout(image_panel)
        image_panel_layout.setContentsMargins(0, 0, 0, 0)
        image_panel_layout.setSpacing(4)
        self.image_gallery = PositionImageGallery()
        self.image_gallery.setMinimumWidth(620)
        image_panel_layout.addWidget(self.image_gallery, 1)
        self.image_panel = image_panel
        self.editor_splitter.addWidget(image_panel)

        details = QWidget()
        details.setMinimumWidth(390)
        details.setMaximumWidth(700)
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(8)

        tags_box = QGroupBox("Tags")
        tags_layout = QVBoxLayout(tags_box)
        self.tags_editor = TagSetEditor(self.config)
        tags_layout.addWidget(self.tags_editor)
        details_layout.addWidget(tags_box, 2)

        self.description_edit = QTextEdit()
        self.description_edit.setPlaceholderText("Short text describing the position")
        self.description_box = QGroupBox("Description — Main image")
        description_layout = QVBoxLayout(self.description_box)
        description_layout.addWidget(self.description_edit)
        details_layout.addWidget(self.description_box, 4)

        metadata_box = QGroupBox("Metadata")
        metadata_layout = QVBoxLayout(metadata_box)
        self.metadata_editor = MetadataKeyValueEditor()
        metadata_layout.addWidget(self.metadata_editor)
        details_layout.addWidget(metadata_box, 2)

        self.details_panel = details
        self.editor_splitter.addWidget(details)
        self.editor_splitter.setStretchFactor(0, 4)
        self.editor_splitter.setStretchFactor(1, 3)
        self.editor_splitter.setSizes([850, 630])
        layout.addWidget(self.editor_splitter, 1)

        self.back_btn.clicked.connect(self.back_requested.emit)
        self.open_folder_btn.clicked.connect(self.open_folder)
        self.delete_btn.clicked.connect(self.delete_current)
        self.score_edit.textChanged.connect(self._on_score_changed)
        self.score_edit.editingFinished.connect(self._normalize_score_input)
        self.score_edit.returnPressed.connect(self._commit_score_input)
        self.score_edit.installEventFilter(self)
        self.description_edit.textChanged.connect(self._on_description_changed)
        self.tags_editor.changed.connect(self.schedule_autosave)
        self.tags_editor.tag_created.connect(lambda _name: self.database_changed.emit())
        self.metadata_editor.changed.connect(self.schedule_autosave)
        self.image_gallery.sgf_board.start_requested.connect(self.set_selected_sgf_start_path)
        self.image_gallery.sgf_board.sgf_edited.connect(self.set_pending_sgf_text)

        self.previous_solution_shortcut = QShortcut(
            QKeySequence("Ctrl+Left"), self,
        )
        self.previous_solution_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.previous_solution_shortcut.activated.connect(
            lambda: self.navigate_solution(-1)
        )
        self.next_solution_shortcut = QShortcut(
            QKeySequence("Ctrl+Right"), self,
        )
        self.next_solution_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.next_solution_shortcut.activated.connect(
            lambda: self.navigate_solution(1)
        )
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._update_solution_shortcuts)
        self._update_solution_shortcuts(None, QApplication.focusWidget())

        self.setEnabled(False)

    def _clear_solution_tabs(self) -> None:
        while self.solution_tabs_layout.count():
            item = self.solution_tabs_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.solution_tab_buttons = []

    def _selected_media_kind(self) -> str:
        if self.selected_image_index == 0:
            return self.main_media_kind
        return self.solution_images[self.selected_image_index - 1].get("kind", "board")

    def _selected_has_image(self) -> bool:
        if self.selected_image_index == 0:
            return not self._main_pixmap().isNull()
        return not self._solution_pixmap(
            self.solution_images[self.selected_image_index - 1]
        ).isNull()

    def _set_selected_media_kind(self, kind: str) -> None:
        if kind not in {"board", "image"}:
            return
        if self.selected_image_index == 0:
            self.main_media_kind = kind
        else:
            self.solution_images[self.selected_image_index - 1]["kind"] = kind
        self.refresh_gallery(self.selected_image_index)
        self.schedule_autosave()

    def _rebuild_baseline_menu(self) -> None:
        menu = QMenu(self.baseline_menu_button)
        menu.addAction("Image from file…", self.choose_selected_image)
        menu.addAction("Image from clipboard", self.paste_selected_image)
        has_sgf = self._has_sgf()
        if has_sgf:
            current_node_action = menu.addAction(
                "Current SGF node", self.set_current_node_as_selected_start
            )
            current_node_action.setEnabled(
                self.image_gallery.sgf_board.current_frame is not None
            )
        else:
            menu.addAction("Create new SGF", lambda: self.create_new_sgf())
        menu.addSeparator()
        show_board = menu.addAction(
            "Display SGF board",
            lambda _checked=False: self._set_selected_media_kind("board"),
        )
        show_board.setCheckable(True)
        show_board.setChecked(self._selected_media_kind() == "board")
        show_board.setEnabled(
            self._current_sgf_path() is not None or self.pending_sgf_text is not None
        )
        show_image = menu.addAction(
            "Display image",
            lambda _checked=False: self._set_selected_media_kind("image"),
        )
        show_image.setCheckable(True)
        show_image.setChecked(self._selected_media_kind() == "image")
        show_image.setEnabled(self._selected_has_image())
        menu.addSeparator()
        delete_solution = QWidgetAction(menu)
        delete_solution.setText("Del — remove selected solution…")
        delete_solution.setToolTip(
            "Remove the selected solution after confirmation; the main position is not deleted."
        )
        delete_solution_button = QPushButton("Del — Remove selected solution…")
        delete_solution_button.setToolTip(delete_solution.toolTip())
        delete_solution_button.setStyleSheet(
            "QPushButton { min-height: 32px; margin: 2px 5px; padding: 0 10px; "
            "text-align: left; border: 1px solid #bd5c70; border-radius: 5px; "
            "background: #f6c7cf; color: #702438; font-weight: 750; }"
            "QPushButton:hover { background: #ecaeb9; border-color: #a94359; }"
            "QPushButton:disabled { background: #f3e8ea; border-color: #d9c9cd; "
            "color: #aa969c; }"
        )
        is_solution = self.selected_image_index > 0
        delete_solution.setEnabled(is_solution)
        delete_solution_button.setEnabled(is_solution)
        delete_solution_button.clicked.connect(menu.close)
        delete_solution_button.clicked.connect(self.remove_selected_solution)
        delete_solution.setDefaultWidget(delete_solution_button)
        menu.addAction(delete_solution)
        menu.addSeparator()
        self.baseline_menu_button.setMenu(menu)
        self.baseline_menu_button.setToolTip(
            f"Selected baseline: {self._selected_media_kind()}"
        )
        self._rebuild_sgf_menu()

    def _has_sgf(self) -> bool:
        return self.pending_sgf_text is not None or self._current_sgf_path() is not None

    def _rebuild_sgf_menu(self) -> None:
        menu = QMenu(self.sgf_menu_btn)
        menu.addAction("Choose SGF from file…", self.choose_sgf)
        menu.addAction("Create new SGF", lambda: self.create_new_sgf())
        if self._has_sgf():
            menu.addSeparator()
            menu.addAction("Remove SGF…", self.mark_clear_sgf)
        self.sgf_menu_btn.setMenu(menu)

    def _rebuild_solution_tabs(self) -> None:
        self._clear_solution_tabs()
        selected = min(self.selected_image_index, len(self.solution_images))
        tab_count = len(self.solution_images) + 1
        if tab_count <= 6:
            visible_indices = list(range(tab_count))
        elif selected <= 5:
            visible_indices = list(range(6))
        elif selected == tab_count - 1:
            visible_indices = [0, 1, 2, selected - 1, selected]
        else:
            visible_indices = [0, 1, selected - 1, selected, selected + 1]
        visible_indices = sorted(set(visible_indices))

        previous_index = -1
        for index in visible_indices:
            if previous_index >= 0 and index > previous_index + 1:
                overflow = QToolButton()
                overflow.setText("…")
                overflow.setPopupMode(QToolButton.InstantPopup)
                overflow.setStyleSheet(
                    "QToolButton { min-height: 34px; max-height: 34px; "
                    "border: none; border-radius: 5px; "
                    "background: transparent; color: #66545a; font-size: 14px; "
                    "font-weight: 700; padding: 0; }"
                    "QToolButton:hover { background: #f8e8ee; }"
                )
                overflow.setFixedSize(30, 34)
                menu = QMenu(overflow)
                for hidden_index in range(previous_index + 1, index):
                    menu.addAction(
                        f"Solution {hidden_index}",
                        lambda _checked=False, tab=hidden_index: self.refresh_gallery(tab),
                    )
                overflow.setMenu(menu)
                self.solution_tabs_layout.addWidget(overflow)
            label = "Main" if index == 0 else f"S{index}"
            button = QPushButton(label)
            button.setMinimumWidth(52 if index == 0 else 40)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCheckable(True)
            button.setChecked(index == selected)
            button.setStyleSheet(
                "QPushButton { min-height: 34px; max-height: 34px; "
                "border: none; border-radius: 5px; "
                "background: transparent; color: #66545a; padding: 0 9px; "
                "font-size: 14px; font-weight: 650; text-align: center; }"
                "QPushButton:hover { background: #f8e8ee; }"
                "QPushButton:checked { background: #f0d3de; color: #673548; "
                "font-weight: 750; }"
            )
            button.setFixedHeight(34)
            button.clicked.connect(lambda _checked=False, tab=index: self.refresh_gallery(tab))
            self.solution_tabs_layout.addWidget(button)
            self.solution_tab_buttons.append(button)
            previous_index = index

        if visible_indices and visible_indices[-1] < tab_count - 1:
            overflow = QToolButton()
            overflow.setText("…")
            overflow.setPopupMode(QToolButton.InstantPopup)
            overflow.setStyleSheet(
                "QToolButton { min-height: 34px; max-height: 34px; "
                "border: none; border-radius: 5px; "
                "background: transparent; color: #66545a; font-size: 14px; "
                "font-weight: 700; padding: 0; }"
                "QToolButton:hover { background: #f8e8ee; }"
            )
            overflow.setFixedSize(30, 34)
            menu = QMenu(overflow)
            for hidden_index in range(visible_indices[-1] + 1, tab_count):
                menu.addAction(
                    f"Solution {hidden_index}",
                    lambda _checked=False, tab=hidden_index: self.refresh_gallery(tab),
                )
            overflow.setMenu(menu)
            self.solution_tabs_layout.addWidget(overflow)

        add_button = QPushButton("+")
        add_button.setFocusPolicy(Qt.NoFocus)
        add_button.setToolTip("Add a solution")
        add_button.setStyleSheet(
            "QPushButton { min-height: 34px; max-height: 34px; padding: 0; "
            "border: none; border-radius: 5px; background: #edf5fa; "
            "color: #356f9f; font-size: 17px; font-weight: 700; }"
            "QPushButton:hover { background: #dcecf5; }"
        )
        add_button.setFixedSize(30, 34)
        add_button.clicked.connect(self.add_solution_board)
        self.solution_tabs_layout.addWidget(add_button)
        self._rebuild_baseline_menu()
        self.solution_controls.updateGeometry()

    def set_available_tags(self, tags: list[str]) -> None:
        self.tags_editor.set_available_tags(tags)

    def _main_pixmap(self) -> QPixmap:
        if self.pending_image is not None and not self.pending_image.isNull():
            return QPixmap.fromImage(self.pending_image)
        path = self.pending_image_path
        if path is None and self.current_position_id:
            path = position_image_path(self.config, self.current_position_id)
        return QPixmap(str(path)) if path and path.exists() else QPixmap()

    def _solution_pixmap(self, solution: dict[str, str]) -> QPixmap:
        relative = solution.get("file", "")
        if not relative:
            return QPixmap()
        pending = self.pending_solution_sources.get(relative)
        if isinstance(pending, QImage):
            return QPixmap.fromImage(pending)
        if isinstance(pending, Path):
            return QPixmap(str(pending))
        if not self.current_position_id:
            return QPixmap()
        path = position_dir(self.config, self.current_position_id) / relative
        return QPixmap(str(path)) if path.exists() else QPixmap()

    def refresh_gallery(self, selected_index: int | None = None) -> None:
        index = self.selected_image_index if selected_index is None else selected_index
        images = [("Main image", self._main_pixmap())]
        board_start_paths: dict[int, list[int]] = {}
        if self.main_media_kind == "board":
            board_start_paths[0] = list(self.main_sgf_start_path)
        for solution_index, solution in enumerate(self.solution_images, start=1):
            score = formatted_score(solution.get("score", ""))
            prefix = "Board solution" if solution.get("kind") == "board" else "Solution"
            title = f"{prefix} {solution_index}" + (f" · {score}" if score else "")
            images.append((title, self._solution_pixmap(solution)))
            if solution.get("kind") == "board":
                board_start_paths[solution_index] = list(solution.get("sgf_start_path", []))
        self.image_gallery.selected_index = min(index, len(images) - 1)
        self.image_gallery.set_images(
            images,
            self._current_sgf_path(),
            self.main_sgf_start_path,
            self.pending_sgf_text,
            board_start_paths,
        )
        self.select_image(self.image_gallery.selected_index)

    def set_pending_sgf_text(self, text: str) -> None:
        self.pending_sgf_text = text
        self.clear_sgf_on_save = False
        self.schedule_autosave()

    def set_selected_sgf_start_path(self, path: list[int]) -> None:
        if self.selected_image_index == 0:
            self.main_sgf_start_path = list(path)
        elif self.selected_image_index - 1 < len(self.solution_images):
            self.solution_images[self.selected_image_index - 1]["sgf_start_path"] = list(path)
        self.image_gallery.board_start_paths[self.selected_image_index] = list(path)
        self.schedule_autosave()

    def set_current_node_as_selected_start(self) -> None:
        frame = self.image_gallery.sgf_board.current_frame
        if frame is None:
            return
        if self.selected_image_index == 0:
            self.main_media_kind = "board"
        else:
            self.solution_images[self.selected_image_index - 1]["kind"] = "board"
        self.set_selected_sgf_start_path(list(frame.node_path))
        self.refresh_gallery(self.selected_image_index)

    def _current_sgf_path(self) -> Path | None:
        if self.clear_sgf_on_save:
            return None
        if self.pending_sgf_path is not None:
            return self.pending_sgf_path
        if self.current_position_id:
            return position_sgf_path(self.config, self.current_position_id)
        return None

    def select_image(self, index: int) -> None:
        if not 0 <= index <= len(self.solution_images):
            return
        self.selected_image_index = index
        self._loading = True
        if index == 0:
            self.description_box.setTitle("Description — Main image")
            self.description_edit.setPlainText(self.main_description)
            self.score_edit.setText(self.main_score)
        else:
            solution = self.solution_images[index - 1]
            self.description_box.setTitle(f"Description — Solution {index}")
            self.description_edit.setPlainText(solution.get("description", ""))
            self.score_edit.setText(solution.get("score", ""))
        self._update_score_style()
        self._loading = False
        self._rebuild_solution_tabs()

    def navigate_solution(self, delta: int) -> None:
        item_count = len(self.solution_images) + 1
        if item_count <= 1:
            return
        self.refresh_gallery((self.selected_image_index + delta) % item_count)

    @staticmethod
    def _has_text_input_focus(widget: QWidget | None) -> bool:
        while widget is not None:
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
                return True
            widget = widget.parentWidget()
        return False

    def _update_solution_shortcuts(
        self, _old: QWidget | None, current: QWidget | None
    ) -> None:
        enabled = not self._has_text_input_focus(current)
        self.previous_solution_shortcut.setEnabled(enabled)
        self.next_solution_shortcut.setEnabled(enabled)

    def _on_description_changed(self) -> None:
        if self._loading:
            return
        text = self.description_edit.toPlainText()
        if self.selected_image_index == 0:
            self.main_description = text
        elif self.selected_image_index - 1 < len(self.solution_images):
            self.solution_images[self.selected_image_index - 1]["description"] = text
        self.schedule_autosave()

    def _on_score_changed(self) -> None:
        if self._loading:
            return
        text = self.score_edit.text().strip()
        if self.selected_image_index == 0:
            self.main_score = text
        elif self.selected_image_index - 1 < len(self.solution_images):
            self.solution_images[self.selected_image_index - 1]["score"] = text
        self._update_score_style()
        self.schedule_autosave()

    def _update_score_style(self) -> None:
        text = self.score_edit.text().strip()
        if self.score_edit.hasFocus() and not self._loading:
            self.score_edit.setStyleSheet("")
            self.score_edit.setToolTip("Enter a signed number, or B +… / W +…; press Enter to commit")
            return
        if not text:
            self.score_edit.setStyleSheet("")
            self.score_edit.setToolTip("Example: B +3.5 or W +6.3")
        elif formatted_score(text):
            self.score_edit.setStyleSheet(score_chip_stylesheet(text, editable=True))
            self.score_edit.setToolTip("Valid score")
        else:
            self.score_edit.setStyleSheet(score_chip_stylesheet(text, editable=True))
            self.score_edit.setToolTip("Use a score such as B +3.5 or W +6.3")

    def _normalize_score_input(self) -> None:
        text = self.score_edit.text().strip()
        canonical = formatted_score(text)
        if canonical and canonical != text:
            self.score_edit.setText(canonical)

    def _commit_score_input(self) -> None:
        self._normalize_score_input()
        self.score_edit.clearFocus()
        QTimer.singleShot(0, self._update_score_style)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if watched is self.score_edit and event.type() in (QEvent.FocusIn, QEvent.FocusOut):
            if event.type() == QEvent.FocusIn:
                self.score_edit.setStyleSheet("")
                self.score_edit.setToolTip(
                    "Enter a signed number, or B +… / W +…; press Enter to commit"
                )
            else:
                self._normalize_score_input()
                QTimer.singleShot(0, self._update_score_style)
        return super().eventFilter(watched, event)

    def clear(self) -> None:
        self.current_position_id = None
        self.pending_image_path = None
        self.pending_image = None
        self.pending_sgf_path = None
        self.pending_sgf_text = None
        self.clear_sgf_on_save = False
        self.pending_solution_sources = {}
        self.solution_files_to_delete = set()
        self.solution_images = []
        self.main_description = ""
        self.main_score = ""
        self.main_media_kind = "board"
        self.main_sgf_start_path = []
        self.selected_image_index = 0
        self.transient_new_position = False
        self.position_id_label.clear()
        self.score_edit.clear()
        self.description_edit.clear()
        self.tags_editor.set_tags([])
        self.metadata_editor.set_metadata({})
        self.save_status_label.clear()
        self.refresh_gallery(0)
        self.setEnabled(False)

    def load_position(self, position_id: str) -> bool:
        self.autosave_timer.stop()
        try:
            record = load_position(self.config, position_id)
            image_path = position_image_path(self.config, position_id)
        except DatabaseError as e:
            QMessageBox.critical(self, "Error", str(e))
            return False

        self._loading = True
        self.current_position_id = position_id
        self.position_id_label.setText(position_id)
        self.transient_new_position = False
        self.pending_image_path = None
        self.pending_image = None
        self.pending_sgf_path = None
        self.pending_sgf_text = None
        self.clear_sgf_on_save = False
        self.pending_solution_sources = {}
        self.solution_files_to_delete = set()
        self.solution_images = [dict(item) for item in record.get("solution_images", [])]
        self.main_description = record.get("description", "")
        self.main_score = record.get("score", "")
        self.main_media_kind = record.get("main_media_kind", "board")
        self.main_sgf_start_path = list(record.get("sgf_start_path", []))
        self.selected_image_index = 0
        self.tags_editor.set_tags(record.get("tags", []))
        self.metadata_editor.set_metadata(record.get("metadata", {}) or {})
        self.save_status_label.setText("All changes saved")
        self.setEnabled(True)
        self.refresh_gallery(0)
        self._loading = False
        return True

    def reload_current(self) -> None:
        if self.current_position_id:
            self.load_position(self.current_position_id)

    def choose_image(self) -> None:
        if not self.current_position_id:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose image", str(self.config.root), IMAGE_FILTER)
        if path:
            self.pending_image_path = Path(path)
            self.pending_image = None
            self.main_media_kind = "image"
            self.refresh_gallery(0)
            self.schedule_autosave()

    def choose_selected_image(self) -> None:
        if self.selected_image_index == 0:
            self.choose_image()
        else:
            self.replace_selected_solution()

    def paste_image(self) -> None:
        if not self.current_position_id:
            return
        clipboard = QApplication.clipboard()
        image = clipboard.image(QClipboard.Clipboard)
        if image.isNull():
            QMessageBox.information(self, "Paste Image", "Clipboard does not currently contain an image.")
            return
        self.pending_image = image
        self.pending_image_path = None
        self.main_media_kind = "image"
        self.refresh_gallery(0)
        self.schedule_autosave()

    def paste_selected_image(self) -> None:
        if self.selected_image_index == 0:
            self.paste_image()
            return
        image = QApplication.clipboard().image(QClipboard.Clipboard)
        if image.isNull():
            QMessageBox.information(self, "Paste Solution", "Clipboard does not currently contain an image.")
            return
        solution = self.solution_images[self.selected_image_index - 1]
        old_relative = solution.get("file", "")
        if old_relative:
            self.pending_solution_sources.pop(old_relative, None)
            self.solution_files_to_delete.add(old_relative)
        relative = self._next_solution_relative(".png")
        solution["kind"] = "image"
        solution["file"] = relative
        self.pending_solution_sources[relative] = image
        self.refresh_gallery(self.selected_image_index)
        self.schedule_autosave()

    def choose_sgf(self) -> None:
        if not self.current_position_id:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose SGF", str(self.config.root), SGF_FILTER)
        if path:
            self.pending_sgf_path = Path(path)
            self.pending_sgf_text = None
            self.clear_sgf_on_save = False
            self.main_media_kind = "board"
            self.main_sgf_start_path = []
            for solution in self.solution_images:
                solution["sgf_start_path"] = []
            self.refresh_gallery(0)
            self.schedule_autosave()

    def create_new_sgf(self, *, confirm: bool = True) -> None:
        if not self.current_position_id:
            return
        if self._has_sgf() and confirm and QMessageBox.question(
            self,
            "Create New SGF",
            "Replace the current SGF with a new blank 19×19 SGF?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        self.pending_sgf_path = None
        self.pending_sgf_text = NEW_SGF_TEXT
        self.clear_sgf_on_save = False
        self.main_media_kind = "board"
        self.main_sgf_start_path = []
        for solution in self.solution_images:
            solution["sgf_start_path"] = []
        self.refresh_gallery(0)
        self.schedule_autosave()

    def mark_clear_sgf(self) -> None:
        if not self.current_position_id:
            return
        self.pending_sgf_path = None
        self.pending_sgf_text = None
        self.clear_sgf_on_save = True
        self.refresh_gallery(0)
        self.schedule_autosave()

    def open_folder(self) -> None:
        if not self.current_position_id:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(position_dir(self.config, self.current_position_id))))

    def _next_solution_relative(self, suffix: str) -> str:
        suffix = suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
        used = {item.get("file", "") for item in self.solution_images}
        number = 1
        while True:
            candidate = f"solutions/solution-{number:03d}{suffix}"
            if candidate not in used:
                return candidate
            number += 1

    def add_solution_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add solution images", str(self.config.root), IMAGE_FILTER)
        for path_text in paths:
            path = Path(path_text)
            relative = self._next_solution_relative(path.suffix)
            current_path = list(self.image_gallery.sgf_board.current_frame.node_path) \
                if self.image_gallery.sgf_board.current_frame else []
            self.solution_images.append({
                "kind": "image",
                "file": relative,
                "description": "",
                "score": "",
                "sgf_start_path": current_path,
            })
            self.pending_solution_sources[relative] = path
        if paths:
            self.refresh_gallery(len(self.solution_images))
            self.schedule_autosave()

    def paste_solution_image(self) -> None:
        image = QApplication.clipboard().image(QClipboard.Clipboard)
        if image.isNull():
            QMessageBox.information(self, "Paste Solution", "Clipboard does not currently contain an image.")
            return
        relative = self._next_solution_relative(".png")
        current_path = list(self.image_gallery.sgf_board.current_frame.node_path) \
            if self.image_gallery.sgf_board.current_frame else []
        self.solution_images.append({
            "kind": "image",
            "file": relative,
            "description": "",
            "score": "",
            "sgf_start_path": current_path,
        })
        self.pending_solution_sources[relative] = image
        self.refresh_gallery(len(self.solution_images))
        self.schedule_autosave()

    def add_solution_board(self) -> None:
        showing_board = (
            self.image_gallery.media_stack.currentWidget() is self.image_gallery.sgf_board
        )
        frame = self.image_gallery.sgf_board.current_frame if showing_board else None
        self.solution_images.append({
            "kind": "board",
            "file": "",
            "description": "",
            "score": "",
            "sgf_start_path": list(frame.node_path) if frame is not None else [],
        })
        self.refresh_gallery(len(self.solution_images))
        self.schedule_autosave()

    def replace_selected_solution(self) -> None:
        if self.selected_image_index == 0:
            QMessageBox.information(self, "Replace Solution", "Select a solution image first.")
            return
        path_text, _ = QFileDialog.getOpenFileName(self, "Replace solution image", str(self.config.root), IMAGE_FILTER)
        if not path_text:
            return
        solution = self.solution_images[self.selected_image_index - 1]
        old_relative = solution.get("file", "")
        if old_relative:
            self.pending_solution_sources.pop(old_relative, None)
            self.solution_files_to_delete.add(old_relative)
        new_relative = self._next_solution_relative(Path(path_text).suffix)
        solution["kind"] = "image"
        solution["file"] = new_relative
        self.pending_solution_sources[new_relative] = Path(path_text)
        self.refresh_gallery(self.selected_image_index)
        self.schedule_autosave()

    def remove_selected_solution(self) -> None:
        if self.selected_image_index == 0:
            QMessageBox.information(self, "Remove Solution", "Select a solution image first.")
            return
        if QMessageBox.question(self, "Remove Solution", "Remove the selected solution?") != QMessageBox.Yes:
            return
        solution = self.solution_images.pop(self.selected_image_index - 1)
        relative = solution.get("file", "")
        if relative:
            self.pending_solution_sources.pop(relative, None)
            self.solution_files_to_delete.add(relative)
        self.refresh_gallery(max(0, self.selected_image_index - 1))
        self.schedule_autosave()

    def delete_current(self) -> None:
        if not self.current_position_id:
            return
        display_name = self.current_position_id
        if QMessageBox.warning(
            self,
            "Delete Position",
            f"Permanently delete '{display_name}' and all of its files?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        position_id = self.current_position_id
        target = position_dir(self.config, position_id).resolve()
        if target.parent != self.config.positions_dir.resolve():
            QMessageBox.critical(self, "Delete Position", "The position folder could not be safely resolved.")
            return
        self.autosave_timer.stop()
        try:
            shutil.rmtree(target)
            GoPositionDatabase(self.config).rebuild_index()
        except Exception as e:
            QMessageBox.critical(self, "Delete Position", str(e))
            return
        self.current_position_id = None
        self.deleted.emit(position_id)

    def _parse_metadata(self) -> dict[str, Any]:
        return self.metadata_editor.metadata()

    def schedule_autosave(self) -> None:
        if self._loading or not self.current_position_id:
            return
        self.save_status_label.setText("Saving changes…")
        self.autosave_timer.start()

    def flush_autosave(self) -> bool:
        if self.autosave_timer.isActive():
            return self.save_current()
        return True

    def has_relevant_data(self) -> bool:
        """Return whether the current draft contains anything worth keeping."""
        if not self.current_position_id:
            return False
        try:
            metadata = self._parse_metadata()
        except DatabaseError:
            # Invalid in-progress input is still user-authored data and must not be discarded.
            return True
        media_dir = position_dir(self.config, self.current_position_id)
        has_main_image = self.pending_image is not None or self.pending_image_path is not None
        has_main_image = has_main_image or position_image_path(self.config, self.current_position_id) is not None
        has_sgf = self.pending_sgf_path is not None or self.pending_sgf_text is not None
        has_sgf = has_sgf or (not self.clear_sgf_on_save and position_sgf_path(self.config, self.current_position_id) is not None)
        return any((
            self.main_description.strip(),
            self.main_score.strip(),
            self.tags_editor.tags(),
            metadata,
            self.solution_images,
            has_main_image,
            has_sgf,
            self.pending_solution_sources,
            media_dir.exists() and any(
                child.is_file() and child.name != self.config.metadata_filename
                for child in media_dir.iterdir()
            ),
        ))

    def discard_empty_new_position(self) -> bool:
        if not self.transient_new_position or not self.current_position_id or self.has_relevant_data():
            return False
        self.autosave_timer.stop()
        target = position_dir(self.config, self.current_position_id).resolve()
        if target.parent != self.config.positions_dir.resolve():
            return False
        shutil.rmtree(target)
        GoPositionDatabase(self.config).rebuild_index()
        self.clear()
        return True

    def _copy_media(self, position_id: str) -> None:
        dest_dir = position_dir(self.config, position_id)
        dest_dir.mkdir(parents=True, exist_ok=True)

        if self.pending_image_path is not None:
            image_bytes = self.pending_image_path.read_bytes()
            image_suffix = self.pending_image_path.suffix
            remove_matching_files(dest_dir, self.config.image_extensions)
            dest = canonical_image_destination(self.config, position_id, image_suffix)
            dest.write_bytes(image_bytes)
        elif self.pending_image is not None:
            remove_matching_files(dest_dir, self.config.image_extensions)
            dest = canonical_image_destination(self.config, position_id, ".png")
            self.pending_image.save(str(dest), "PNG")

        if self.clear_sgf_on_save:
            remove_matching_files(dest_dir, self.config.sgf_extensions)
        elif self.pending_sgf_text is not None:
            remove_matching_files(dest_dir, self.config.sgf_extensions)
            dest = dest_dir / self.config.sgf_filename
            dest.write_text(self.pending_sgf_text, encoding="utf-8")
        elif self.pending_sgf_path is not None:
            sgf_bytes = self.pending_sgf_path.read_bytes()
            remove_matching_files(dest_dir, self.config.sgf_extensions)
            dest = dest_dir / self.config.sgf_filename
            dest.write_bytes(sgf_bytes)

        pending_payloads: list[tuple[Path, bytes | QImage]] = []
        for relative, source in self.pending_solution_sources.items():
            target = (dest_dir / relative).resolve()
            if dest_dir.resolve() not in target.parents:
                raise DatabaseError(f"Unsafe solution image path: {relative}")
            payload: bytes | QImage = source.read_bytes() if isinstance(source, Path) else source
            pending_payloads.append((target, payload))

        for relative in self.solution_files_to_delete:
            target = (dest_dir / relative).resolve()
            if dest_dir.resolve() not in target.parents:
                raise DatabaseError(f"Unsafe solution image path: {relative}")
            target.unlink(missing_ok=True)

        for target, payload in pending_payloads:
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(payload, QImage):
                if not payload.save(str(target), "PNG"):
                    raise DatabaseError(f"Could not save solution image: {target}")
            else:
                target.write_bytes(payload)

    def save_current(self) -> bool:
        if not self.current_position_id:
            return False
        self.autosave_timer.stop()
        self.save_status_label.setText("Saving changes…")
        try:
            graph = GoPositionDatabase(self.config).tag_graph()
            tags = minimal_explicit_tags(graph, self.tags_editor.tags())
            self.tags_editor.set_tags(tags)
            main_score = formatted_score(self.main_score) or self.main_score.strip()
            solution_records: list[dict[str, str]] = []
            for item in self.solution_images:
                normalized = dict(item)
                raw_score = normalized.get("score", "").strip()
                normalized["score"] = formatted_score(raw_score) or raw_score
                solution_records.append(normalized)
            record = {
                "description": self.main_description.strip(),
                "score": main_score,
                "main_media_kind": self.main_media_kind,
                "sgf_start_path": list(self.main_sgf_start_path),
                "tags": tags,
                "metadata": self._parse_metadata(),
                "solution_images": solution_records,
            }
            self._copy_media(self.current_position_id)
            save_position(self.config, self.current_position_id, record)
            db = GoPositionDatabase(self.config)
            db.rebuild_index()
        except DatabaseError as e:
            self.save_status_label.setText("Changes not saved")
            QMessageBox.critical(self, "Save Error", str(e))
            return False
        except Exception as e:
            self.save_status_label.setText("Changes not saved")
            QMessageBox.critical(self, "Save Error", str(e))
            return False

        position_id = self.current_position_id
        self.pending_image_path = None
        self.pending_image = None
        self.pending_sgf_path = None
        self.pending_sgf_text = None
        self.clear_sgf_on_save = False
        self.pending_solution_sources = {}
        self.solution_files_to_delete = set()
        self.save_status_label.setText("All changes saved")
        self.saved.emit(position_id)
        return True


class NewPositionDialog(QDialog):
    created = Signal(str)

    def __init__(self, config, tag_names: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.pending_clipboard_image: QImage | None = None
        self.setWindowTitle("Create New Position")
        self.resize(700, 700)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        id_row = QHBoxLayout()
        self.position_id_edit = QLineEdit(suggest_next_position_id(config))
        self.suggest_button = QPushButton("Suggest Next")
        id_row.addWidget(self.position_id_edit)
        id_row.addWidget(self.suggest_button)
        form.addRow("Position ID", self._wrap_layout(id_row))

        image_row = QHBoxLayout()
        self.image_path_edit = QLineEdit()
        self.image_path_edit.setPlaceholderText("Choose an image file, or use Paste Image")
        self.image_browse_btn = QPushButton("Browse")
        self.image_paste_btn = QPushButton("Paste Image")
        image_row.addWidget(self.image_path_edit)
        image_row.addWidget(self.image_browse_btn)
        image_row.addWidget(self.image_paste_btn)
        form.addRow("Image", self._wrap_layout(image_row))

        sgf_row = QHBoxLayout()
        self.sgf_path_edit = QLineEdit()
        self.sgf_path_edit.setPlaceholderText("Optional SGF")
        self.sgf_browse_btn = QPushButton("Browse")
        sgf_row.addWidget(self.sgf_path_edit)
        sgf_row.addWidget(self.sgf_browse_btn)
        form.addRow("SGF", self._wrap_layout(sgf_row))

        self.preview_label = QLabel("No image selected")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(260, 260)
        self.preview_label.setStyleSheet("background: #f4f4f4; border: 1px solid #cccccc;")
        form.addRow("Preview", self.preview_label)

        self.description_edit = QTextEdit()
        self.description_edit.setFixedHeight(100)
        form.addRow("Description", self.description_edit)

        self.tags_editor = TagSetEditor(config)
        self.tags_editor.set_available_tags(tag_names)
        form.addRow("Explicit Tags", self.tags_editor)

        self.metadata_editor = MetadataKeyValueEditor()
        form.addRow("Metadata", self.metadata_editor)
        layout.addLayout(form)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(self.button_box)

        self.suggest_button.clicked.connect(self._set_suggested_id)
        self.image_browse_btn.clicked.connect(self._browse_image)
        self.image_paste_btn.clicked.connect(self._paste_image)
        self.sgf_browse_btn.clicked.connect(self._browse_sgf)
        self.button_box.accepted.connect(self.create_position)
        self.button_box.rejected.connect(self.reject)

    def _wrap_layout(self, layout) -> QWidget:
        w = QWidget()
        w.setLayout(layout)
        return w

    def _set_suggested_id(self) -> None:
        self.position_id_edit.setText(suggest_next_position_id(self.config))

    def _browse_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose image", str(self.config.root), IMAGE_FILTER)
        if path:
            self.image_path_edit.setText(path)
            self.pending_clipboard_image = None
            self._update_preview(path=Path(path))

    def _paste_image(self) -> None:
        clipboard = QApplication.clipboard()
        image = clipboard.image(QClipboard.Clipboard)
        if image.isNull():
            QMessageBox.information(self, "Paste Image", "Clipboard does not currently contain an image.")
            return
        self.pending_clipboard_image = image
        self.image_path_edit.clear()
        self._update_preview(image=image)

    def _browse_sgf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose SGF", str(self.config.root), SGF_FILTER)
        if path:
            self.sgf_path_edit.setText(path)

    def _update_preview(self, path: Path | None = None, image: QImage | None = None) -> None:
        if image is not None and not image.isNull():
            pix = QPixmap.fromImage(image)
        elif path is not None:
            pix = QPixmap(str(path))
        else:
            pix = QPixmap()
        if pix.isNull():
            self.preview_label.setText("No image selected")
            self.preview_label.setPixmap(QPixmap())
            return
        self.preview_label.setPixmap(pix.scaled(280, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _parse_metadata(self) -> dict[str, Any]:
        return self.metadata_editor.metadata()

    def create_position(self) -> None:
        pid = self.position_id_edit.text().strip()
        if not pid:
            QMessageBox.critical(self, "Error", "Position ID cannot be empty.")
            return
        dest = position_dir(self.config, pid)
        if dest.exists():
            QMessageBox.critical(self, "Error", f"Position '{pid}' already exists.")
            return
        if not self.image_path_edit.text().strip() and self.pending_clipboard_image is None:
            QMessageBox.critical(self, "Error", "Please choose or paste an image.")
            return
        try:
            graph = GoPositionDatabase(self.config).tag_graph()
            tags = []
            for tag in self.tags_editor.tags():
                canonical = graph.canonical(tag)
                if canonical not in tags:
                    tags.append(canonical)
            metadata = self._parse_metadata()
            dest.mkdir(parents=True, exist_ok=False)
            if self.pending_clipboard_image is not None:
                image_dest = canonical_image_destination(self.config, pid, ".png")
                self.pending_clipboard_image.save(str(image_dest), "PNG")
            else:
                image_source = Path(self.image_path_edit.text().strip())
                image_dest = canonical_image_destination(self.config, pid, image_source.suffix)
                image_dest.write_bytes(image_source.read_bytes())

            sgf_text = self.sgf_path_edit.text().strip()
            if sgf_text:
                sgf_source = Path(sgf_text)
                (dest / self.config.sgf_filename).write_bytes(sgf_source.read_bytes())

            save_position(self.config, pid, {
                "description": self.description_edit.toPlainText().strip(),
                "tags": tags,
                "metadata": metadata,
            })
            db = GoPositionDatabase(self.config)
            db.rebuild_index()
        except Exception as e:
            QMessageBox.critical(self, "Create Error", str(e))
            if dest.exists() and not position_metadata_path(self.config, pid).exists():
                # best-effort rollback of a partially created directory
                try:
                    for child in dest.iterdir():
                        if child.is_file():
                            child.unlink()
                    dest.rmdir()
                except Exception:
                    pass
            return

        self.created.emit(pid)
        self.accept()


class TagManagerPage(QWidget):
    changed = Signal()
    back_requested = Signal()

    def __init__(self, config, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.loaded_tag_name: str | None = None
        self._loading_description = False
        self.description_save_timer = QTimer(self)
        self.description_save_timer.setSingleShot(True)
        self.description_save_timer.setInterval(700)
        self.description_save_timer.timeout.connect(self.save_description)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        header = QHBoxLayout()
        self.back_btn = QPushButton("Back to browse")
        title = QLabel("Manage tags")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        header.addWidget(self.back_btn)
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)
        body = QHBoxLayout()
        layout.addLayout(body, 1)

        left = QVBoxLayout()
        self.filter_edit = TagQueryLineEdit()
        self.filter_edit.setPlaceholderText("Find a tag, or type a new one and press Enter")
        left.addWidget(self.filter_edit)

        self.tag_list = QListWidget()
        configure_tag_chip_list(self.tag_list)
        self.tag_list.setSelectionMode(QAbstractItemView.SingleSelection)
        left.addWidget(self.tag_list, 1)

        self.delete_tag_btn = QPushButton("Delete Tag")
        left.addWidget(self.delete_tag_btn)
        left_widget = QWidget()
        left_widget.setLayout(left)
        body.addWidget(left_widget, 1)

        right = QVBoxLayout()
        self.tag_name_label = QLabel("No tag selected")
        right.addWidget(self.tag_name_label)
        self.tag_stats_label = QLabel("")
        self.tag_stats_label.setStyleSheet("color: #5f6368; font-weight: 600;")
        right.addWidget(self.tag_stats_label)

        self.tag_description_edit = QTextEdit()
        self.tag_description_edit.setPlaceholderText("Optional tag description")
        self.tag_description_edit.setMinimumHeight(145)
        right.addWidget(QLabel("Description"))
        right.addWidget(self.tag_description_edit)
        self.description_status_label = QLabel("")
        self.description_status_label.setStyleSheet("color: #777; font-size: 12px;")
        right.addWidget(self.description_status_label)

        self.parents_list = QListWidget()
        self.children_list = QListWidget()
        configure_tag_chip_list(self.parents_list)
        configure_tag_chip_list(self.children_list)

        parent_box = QGroupBox("Parents")
        parent_layout = QVBoxLayout(parent_box)
        parent_layout.addWidget(self.parents_list)
        parent_row = QHBoxLayout()
        self.add_parent_btn = QPushButton("Add Parent")
        self.remove_parent_btn = QPushButton("Remove Selected Parent(s)")
        parent_row.addWidget(self.add_parent_btn)
        parent_row.addWidget(self.remove_parent_btn)
        parent_layout.addLayout(parent_row)
        right.addWidget(parent_box)

        child_box = QGroupBox("Children")
        child_layout = QVBoxLayout(child_box)
        child_layout.addWidget(self.children_list)
        child_row = QHBoxLayout()
        self.add_child_btn = QPushButton("Add Child")
        self.remove_child_btn = QPushButton("Remove Selected Child(ren)")
        child_row.addWidget(self.add_child_btn)
        child_row.addWidget(self.remove_child_btn)
        child_layout.addLayout(child_row)
        right.addWidget(child_box)

        right.addStretch(1)

        right_widget = QWidget()
        right_widget.setLayout(right)
        body.addWidget(right_widget, 2)

        self.back_btn.clicked.connect(self.back_requested.emit)
        self.filter_edit.textChanged.connect(self.apply_filter)
        self.filter_edit.returnPressed.connect(self.select_or_create_filter_tag)
        self.tag_list.currentTextChanged.connect(self.load_selected_tag)
        self.delete_tag_btn.clicked.connect(self.delete_tag)
        self.add_parent_btn.clicked.connect(self.add_parent)
        self.remove_parent_btn.clicked.connect(self.remove_parent)
        self.add_child_btn.clicked.connect(self.add_child)
        self.remove_child_btn.clicked.connect(self.remove_child)
        self.tag_description_edit.textChanged.connect(self.schedule_description_save)

    def refresh(self) -> None:
        self.flush_description_save()
        self.graph = TagGraph(self.config)
        self.all_tag_names = self.graph.names()
        self.filter_edit.set_tag_names(self.all_tag_names)
        self.apply_filter()

    def apply_filter(self) -> None:
        current = self.tag_list.currentItem().text() if self.tag_list.currentItem() else None
        needle = self.filter_edit.text().strip().casefold()
        self.tag_list.clear()
        for name in self.all_tag_names:
            if not needle or needle in name.casefold():
                self.tag_list.addItem(name)
        if current:
            matches = self.tag_list.findItems(current, Qt.MatchExactly)
            if matches:
                self.tag_list.setCurrentItem(matches[0])

    def load_selected_tag(self, name: str) -> None:
        if name != self.loaded_tag_name:
            self.flush_description_save()
        if not name:
            self.loaded_tag_name = None
            self.tag_name_label.setText("No tag selected")
            self.tag_stats_label.clear()
            self._loading_description = True
            self.tag_description_edit.clear()
            self._loading_description = False
            self.description_status_label.clear()
            self.parents_list.clear()
            self.children_list.clear()
            return
        info = self.graph.info(name)
        self.loaded_tag_name = info.name
        self.tag_name_label.setText(f"<b>{info.name}</b>")
        db = GoPositionDatabase(self.config)
        positions = db.all_positions()
        direct_count = sum(
            1 for record in positions.values()
            if any(self.graph.has(tag) and self.graph.canonical(tag) == info.name for tag in record.get("tags", []))
        )
        matching_count = len(db.search(info.name))
        self.tag_stats_label.setText(
            f"{matching_count} matching position(s) · {direct_count} directly tagged"
        )
        self._loading_description = True
        self.tag_description_edit.setPlainText(info.description)
        self._loading_description = False
        self.description_status_label.setText("Saved automatically")
        self.parents_list.clear()
        for parent in self.graph.parents(name):
            self.parents_list.addItem(parent)
        self.children_list.clear()
        child_map = self.graph.children_map()
        for child in child_map.get(info.name, []):
            item = QListWidgetItem(child)
            item.setToolTip(f"Parents: {', '.join(self.graph.parents(child)) or '(none)'}")
            self.children_list.addItem(item)

    def _current_tag(self) -> str | None:
        item = self.tag_list.currentItem()
        return item.text() if item else None

    def select_or_create_filter_tag(self) -> None:
        raw_name = self.filter_edit.text().strip()
        if not raw_name:
            return
        try:
            name = validate_new_tag_name(raw_name)
            if self.graph.has(name):
                name = self.graph.canonical(name)
            else:
                self.graph.add(name)
                GoPositionDatabase(self.config).rebuild_index()
                self.changed.emit()
                self.all_tag_names = self.graph.names()
                self.filter_edit.set_tag_names(self.all_tag_names)
            self.filter_edit.setText(name)
            self.apply_filter()
            matches = self.tag_list.findItems(name, Qt.MatchExactly)
            if matches:
                self.tag_list.setCurrentItem(matches[0])
                self.tag_list.scrollToItem(matches[0])
        except Exception as e:
            QMessageBox.critical(self, "Tag", str(e))

    def new_tag(self) -> None:
        name, ok = QInputDialog.getText(self, "New Tag", "Tag name:")
        if not ok or not name.strip():
            return
        description, _ = QInputDialog.getText(self, "New Tag", "Optional description:")
        try:
            self.graph.add(name.strip(), description=description)
            GoPositionDatabase(self.config).rebuild_index()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self.changed.emit()
        self.refresh()
        matches = self.tag_list.findItems(normalize_tag_name(name), Qt.MatchExactly)
        if matches:
            self.tag_list.setCurrentItem(matches[0])

    def delete_tag(self) -> None:
        name = self._current_tag()
        if not name:
            return
        self.flush_description_save()
        db = GoPositionDatabase(self.config)
        positions = db.all_positions()
        affected = [
            position_id for position_id, record in positions.items()
            if any(
                self.graph.has(tag) and self.graph.canonical(tag) == name
                for tag in record.get("tags", [])
            )
        ]
        if affected:
            reference_note = (
                f"It will also be removed from {len(affected)} directly tagged "
                f"position{'s' if len(affected) != 1 else ''}."
            )
            if QMessageBox.question(
                self, "Delete Tag", f"Delete tag '{name}'?\n\n{reference_note}"
            ) != QMessageBox.Yes:
                return
        try:
            db.delete_tag(name)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self.loaded_tag_name = None
        self.filter_edit.clear()
        self.changed.emit()
        self.refresh()
        self.load_selected_tag("")

    def add_parent(self) -> None:
        name = self._current_tag()
        if not name:
            return
        choices = [tag for tag in self.graph.names() if tag != name and tag not in self.graph.parents(name)]
        if not choices:
            QMessageBox.information(self, "Add Parent", "No available parent tags.")
            return
        parent, ok = QInputDialog.getItem(self, "Add Parent", f"Choose a parent for '{name}':", choices, 0, False)
        if not ok or not parent:
            return
        try:
            self.graph.add_parent(name, parent)
            GoPositionDatabase(self.config).rebuild_index()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self.changed.emit()
        self.refresh()
        self.load_selected_tag(name)

    def remove_parent(self) -> None:
        name = self._current_tag()
        if not name:
            return
        parents = [item.text() for item in self.parents_list.selectedItems()]
        if not parents:
            QMessageBox.information(self, "Remove Parent", "Select at least one parent to remove.")
            return
        try:
            for parent in parents:
                self.graph.remove_parent(name, parent)
            GoPositionDatabase(self.config).rebuild_index()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self.changed.emit()
        self.refresh()
        self.load_selected_tag(name)

    def add_child(self) -> None:
        name = self._current_tag()
        if not name:
            return
        current_children = set(self.graph.children_map().get(name, []))
        choices = [tag for tag in self.graph.names() if tag != name and tag not in current_children]
        if not choices:
            QMessageBox.information(self, "Add Child", "No available child tags.")
            return
        child, ok = QInputDialog.getItem(self, "Add Child", f"Choose a child for '{name}':", choices, 0, False)
        if not ok or not child:
            return
        try:
            self.graph.add_child(name, child)
            GoPositionDatabase(self.config).rebuild_index()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self.changed.emit()
        self.refresh()
        self.load_selected_tag(name)

    def remove_child(self) -> None:
        name = self._current_tag()
        if not name:
            return
        children = [item.text() for item in self.children_list.selectedItems()]
        if not children:
            QMessageBox.information(self, "Remove Child", "Select at least one child to remove.")
            return
        try:
            for child in children:
                self.graph.remove_child(name, child)
            GoPositionDatabase(self.config).rebuild_index()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self.changed.emit()
        self.refresh()
        self.load_selected_tag(name)

    def save_description(self) -> None:
        name = self.loaded_tag_name
        if not name:
            return
        desc = self.tag_description_edit.toPlainText().strip()
        raw = self.graph.entries[name] or {}
        raw["description"] = desc
        self.graph.entries[name] = raw
        try:
            self.graph._save_entries()
            GoPositionDatabase(self.config).rebuild_index()
        except Exception as e:
            self.description_status_label.setText("Could not save")
            QMessageBox.critical(self, "Error", str(e))
            return
        self.description_status_label.setText("Saved automatically")
        self.changed.emit()

    def schedule_description_save(self) -> None:
        if self._loading_description or not self.loaded_tag_name:
            return
        self.description_status_label.setText("Saving…")
        self.description_save_timer.start()

    def flush_description_save(self) -> bool:
        if not self.description_save_timer.isActive():
            return True
        self.description_save_timer.stop()
        self.save_description()
        return self.description_status_label.text() != "Could not save"


class MainWindow(QMainWindow):
    def __init__(self, root: Path | None = None, config_path: Path | None = None):
        super().__init__()
        self.config = load_config(root=root or DEFAULT_ROOT, config_path=config_path)
        self.db = GoPositionDatabase(self.config)
        self.results_dirty = False
        self.maintenance_issues: list[str] = []
        self.setWindowTitle(f"Browse — Go Position DB — {self.config.root}")
        self.resize(1500, 900)
        self.setStyleSheet("""
            QWidget { font-size: 15px; }
            QMainWindow, QStackedWidget { background: #f6f2f1; }
            QPushButton, QToolButton, QLineEdit {
                min-height: 34px;
                padding: 3px 10px;
            }
            QPushButton, QToolButton {
                background: #fffdfd; border: 1px solid #cfc5c8; border-radius: 8px;
            }
            QPushButton:hover, QToolButton:hover { background: #f8e8ee; border-color: #d49aad; }
            QPushButton:pressed, QToolButton:pressed { background: #f0d5df; }
            QLineEdit, QTextEdit, QPlainTextEdit, QTableWidget {
                background: #fffdfd; border: 1px solid #cfc5c8; border-radius: 8px;
                selection-background-color: #d99bb2;
            }
            QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QTableWidget:focus {
                border: 2px solid #7fa6c4;
            }
            QGroupBox {
                font-weight: 650; border: 1px solid #ddd3d6; border-radius: 10px;
                margin-top: 10px; padding-top: 10px; background: rgba(255,255,255,105);
            }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #5c4650; }
            QFrame#searchResultCard {
                background: #fffdfd; border: 1px solid #d8ced1; border-radius: 10px;
            }
            QMenu { font-size: 15px; padding: 5px; background: #fffdfd; border: 1px solid #cfc5c8; }
            QMenu::item { padding: 7px 24px; border-radius: 5px; }
            QMenu::item:selected { background: #f3dce4; }
            QStatusBar { background: #eee8e9; color: #665a5e; }
        """)
        self._build_ui()
        self.refresh_tag_names()
        self._automatic_maintenance()
        self.refresh_status_bar()
        self.run_search()

    def _build_ui(self) -> None:
        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)

        self.search_page = QWidget()
        root_layout = QVBoxLayout(self.search_page)
        root_layout.setContentsMargins(10, 8, 10, 8)
        root_layout.setSpacing(6)

        nav = QHBoxLayout()
        self.browse_nav_btn = QPushButton("Browse positions")
        self.browse_nav_btn.setEnabled(False)
        self.new_nav_btn = QPushButton("New position")
        self.tags_nav_btn = QPushButton("Manage tags")
        nav.addWidget(self.browse_nav_btn)
        nav.addWidget(self.new_nav_btn)
        nav.addWidget(self.tags_nav_btn)
        nav.addStretch(1)
        root_layout.addLayout(nav)

        search_row = QHBoxLayout()
        self.query_edit = TagQueryLineEdit()
        self.query_edit.set_boolean_query_mode()
        self.query_edit.setPlaceholderText("Search, e.g. (joseki AND reverse-sente) OR large-reverse-sente; leave blank to list all positions")
        self.query_edit.setMinimumWidth(540)
        self.query_edit.setMaximumWidth(600)
        self.search_btn = QPushButton("Search")
        self.current_display_mode = "Standard"
        self.display_group = QButtonGroup(self)
        display_switch = QFrame()
        display_switch.setStyleSheet(
            "QFrame { border: 1px solid #cfc5c8; border-radius: 8px; background: #fffafa; }"
            "QPushButton { border: none; border-radius: 5px; min-width: 105px; min-height: 30px; padding: 2px 12px; }"
            "QPushButton:checked { background: #f0d3de; color: #673548; font-weight: 700; }"
        )
        display_layout = QHBoxLayout(display_switch)
        display_layout.setContentsMargins(2, 2, 2, 2)
        display_layout.setSpacing(1)
        self.display_buttons: dict[str, QPushButton] = {}
        for mode_name in DISPLAY_MODES:
            button = QPushButton(mode_name)
            button.setCheckable(True)
            button.setChecked(mode_name == self.current_display_mode)
            self.display_group.addButton(button)
            self.display_buttons[mode_name] = button
            display_layout.addWidget(button)
            button.clicked.connect(
                lambda checked=False, name=mode_name: self.set_display_mode(name) if checked else None
            )
        search_row.addWidget(QLabel("Query"))
        search_row.addWidget(self.query_edit)
        search_row.addWidget(self.search_btn)
        search_row.addStretch(1)
        search_row.addWidget(display_switch)
        root_layout.addLayout(search_row)

        self.feedback_label = QLabel("")
        root_layout.addWidget(self.feedback_label)

        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setFrameShape(QFrame.NoFrame)
        self.results_container = QWidget()
        self.results_layout = QGridLayout(self.results_container)
        self.results_layout.setAlignment(Qt.AlignTop)
        self.results_layout.setHorizontalSpacing(12)
        self.results_layout.setVerticalSpacing(12)
        self.results_scroll.setWidget(self.results_container)
        root_layout.addWidget(self.results_scroll, 1)

        self.editor = PositionEditor(self.config)
        self.tag_manager = TagManagerPage(self.config)
        self.statusBar().addPermanentWidget(self.editor.save_status_label)
        self.editor.save_status_label.setEnabled(True)
        self.pages.addWidget(self.search_page)
        self.pages.addWidget(self.editor)
        self.pages.addWidget(self.tag_manager)
        self.pages.setCurrentWidget(self.search_page)

        self.search_btn.clicked.connect(self.run_search)
        self.query_edit.returnPressed.connect(self.run_search)
        self.new_nav_btn.clicked.connect(self.create_position)
        self.tags_nav_btn.clicked.connect(self.open_tag_manager)
        self.editor.back_requested.connect(self.show_search)
        self.editor.saved.connect(self._after_editor_saved)
        self.editor.database_changed.connect(self._after_database_change)
        self.editor.deleted.connect(self._after_deleted_position)
        self.tag_manager.back_requested.connect(self.show_search)
        self.tag_manager.changed.connect(self._after_database_change)

    def _automatic_maintenance(self) -> None:
        issues: list[str] = []
        for position_id in iter_position_ids(self.config):
            try:
                clean_position_files(self.config, position_id)
                # Rewrite through the current schema (including removal of retired fields).
                save_position(self.config, position_id, load_position(self.config, position_id))
            except Exception as e:
                issues.append(str(e))
        try:
            self.db.rebuild_index()
        except Exception as e:
            issues.append(f"The search index could not be updated: {e}")
        try:
            issues.extend(self.db.check())
        except Exception as e:
            issues.append(f"The consistency check could not finish: {e}")
        self.maintenance_issues = list(dict.fromkeys(issues))

    def refresh_tag_names(self) -> None:
        try:
            graph = self.db.tag_graph()
            tag_names = graph.names()
        except Exception:
            tag_names = []
        self.query_edit.set_tag_names(tag_names)
        self.editor.set_available_tags(tag_names)

    def refresh_status_bar(self) -> None:
        pos_count = len(iter_position_ids(self.config))
        git_text = self._git_status_text()
        maintenance_text = (
            f" | {len(self.maintenance_issues)} maintenance issue(s)"
            if self.maintenance_issues else ""
        )
        self.statusBar().showMessage(f"{self.config.root} | {pos_count} positions | {git_text}{maintenance_text}")
        self.statusBar().setToolTip("\n".join(self.maintenance_issues))

    def _git_status_text(self) -> str:
        try:
            inside = subprocess.run(
                ["git", "-C", str(self.config.root), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                check=False,
            )
            if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
                return "Git: not a repo"
            status = subprocess.run(
                ["git", "-C", str(self.config.root), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=False,
            )
            lines = [line for line in status.stdout.splitlines() if line.strip()]
            return f"Git: {len(lines)} uncommitted change(s)"
        except Exception:
            return "Git: unavailable"

    def clear_results(self) -> None:
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for column in range(8):
            self.results_layout.setColumnStretch(column, 0)

    def set_display_mode(self, mode_name: str) -> None:
        if mode_name not in DISPLAY_MODES:
            return
        self.current_display_mode = mode_name
        self.display_buttons[mode_name].setChecked(True)
        self.run_search()

    def run_search(self) -> None:
        query = self.query_edit.text().strip()
        try:
            if not query:
                ids = iter_position_ids(self.config)
            else:
                ids = self.db.search(query)
        except Exception as e:
            self.feedback_label.setText(f"Search error: {e}")
            self.clear_results()
            return

        self.clear_results()
        mode = DISPLAY_MODES[self.current_display_mode]
        for index, pid in enumerate(ids):
            try:
                record = load_position(self.config, pid)
                image_path = position_image_path(self.config, pid)
                sgf_path = position_sgf_path(self.config, pid)
            except Exception:
                continue
            card = SearchResultCard(pid, record, image_path, mode, sgf_path=sgf_path)
            card.open_requested.connect(self.open_position)
            row, column = divmod(index, mode.columns)
            self.results_layout.addWidget(card, row, column)
        for column in range(mode.columns):
            self.results_layout.setColumnStretch(column, 1)
        count = len(ids)
        query_summary = query if query else "(all positions)"
        self.feedback_label.setText(
            f"{count} position(s) shown for {query_summary}. Click an image or double-click a result to edit."
        )
        self.results_dirty = False
        self.refresh_status_bar()

    def open_position(self, position_id: str) -> None:
        if self.editor.load_position(position_id):
            self.pages.setCurrentWidget(self.editor)
            self.setWindowTitle(f"Edit {position_id} — Go Position DB")

    def show_search(self) -> None:
        current_page = self.pages.currentWidget()
        if current_page is self.editor:
            try:
                discarded = self.editor.discard_empty_new_position()
            except Exception as e:
                QMessageBox.critical(self, "New Position", f"The empty draft could not be removed:\n{e}")
                return
            if not discarded and not self.editor.flush_autosave():
                return
        elif current_page is self.tag_manager and not self.tag_manager.flush_description_save():
            return
        self.run_search()
        self.pages.setCurrentWidget(self.search_page)
        self.setWindowTitle(f"Browse — Go Position DB — {self.config.root}")
        self.query_edit.setFocus()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        current_page = self.pages.currentWidget()
        if current_page is self.editor:
            try:
                discarded = self.editor.discard_empty_new_position()
            except Exception:
                discarded = False
            if not discarded and not self.editor.flush_autosave():
                event.ignore()
                return
        elif current_page is self.tag_manager and not self.tag_manager.flush_description_save():
            event.ignore()
            return
        event.accept()

    def _after_editor_saved(self, position_id: str) -> None:
        self.refresh_tag_names()
        self.refresh_status_bar()
        self.results_dirty = True
        self.setWindowTitle(f"Edit {position_id} — Go Position DB")

    def _after_database_change(self) -> None:
        self.refresh_tag_names()
        self.refresh_status_bar()

    def create_position(self) -> None:
        position_id = suggest_next_position_id(self.config)
        destination = position_dir(self.config, position_id)
        try:
            destination.mkdir(parents=True, exist_ok=False)
            save_position(self.config, position_id, {
                "description": "",
                "score": "",
                "main_media_kind": "board",
                "sgf_start_path": [],
                "tags": [],
                "metadata": {},
                "solution_images": [],
            })
            self.db.rebuild_index()
        except Exception as e:
            if destination.exists() and not position_metadata_path(self.config, position_id).exists():
                shutil.rmtree(destination, ignore_errors=True)
            QMessageBox.critical(self, "New Position", str(e))
            return
        self._after_created_position(position_id)
        self.editor.transient_new_position = True

    def _after_created_position(self, position_id: str) -> None:
        self.refresh_tag_names()
        self.refresh_status_bar()
        self.run_search()
        self.open_position(position_id)
        self.editor.score_edit.setFocus()

    def _after_deleted_position(self, position_id: str) -> None:
        self.editor.clear()
        self.refresh_tag_names()
        self.refresh_status_bar()
        self.run_search()
        self.pages.setCurrentWidget(self.search_page)
        self.setWindowTitle(f"Browse — Go Position DB — {self.config.root}")

    def open_tag_manager(self) -> None:
        self.tag_manager.refresh()
        self.pages.setCurrentWidget(self.tag_manager)
        self.setWindowTitle("Manage tags — Go Position DB")

    def rebuild_index(self) -> None:
        try:
            data = self.db.rebuild_index()
        except Exception as e:
            QMessageBox.critical(self, "Rebuild Index", str(e))
            return
        QMessageBox.information(self, "Rebuild Index", f"Rebuilt index for {len(data['position_to_expanded_tags'])} position(s).")
        self.refresh_status_bar()

    def check_database(self) -> None:
        errors = self.db.check()
        if not errors:
            QMessageBox.information(self, "Check Database", "Database is internally consistent.")
            return
        msg = "\n".join(f"- {e}" for e in errors)
        box = QMessageBox(self)
        box.setWindowTitle("Check Database")
        box.setIcon(QMessageBox.Warning)
        box.setText("Database has validation issues.")
        box.setDetailedText(msg)
        box.exec()


def run_gui(root: Path | None = None, config_path: Path | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow(root=root, config_path=config_path)
    win.show()
    return app.exec()
