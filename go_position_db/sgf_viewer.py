from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pysgf import GoGame, Move, ParseError
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolButton, QWidget

# Board layout, expressed as multiples of one grid step. The coordinate labels sit
# in COORD_ROOM on the top and left; a stone's outer half sits in STONE_ROOM on the
# bottom and right. BOARD_MARGIN is bare wood and is kept equal on all four sides.
COORD_ROOM_RATIO = 1.16
STONE_ROOM_RATIO = 0.46
BOARD_MARGIN_RATIO = 0.022
BOARD_MARGIN_MIN = 7.0


@dataclass(frozen=True)
class SgfFrame:
    stones: tuple[tuple[int, int, str, int | None], ...]
    move_number: int
    last_move: tuple[int, int] | None
    last_player: str | None
    comment: str
    node_path: tuple[int, ...]
    labels: tuple[tuple[int, int, str], ...] = ()
    triangles: tuple[tuple[int, int], ...] = ()
    circles: tuple[tuple[int, int], ...] = ()
    squares: tuple[tuple[int, int], ...] = ()
    crosses: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class SgfPlayback:
    board_size: tuple[int, int]
    frames: tuple[SgfFrame, ...]
    start_frame_index: int
    start_path_valid: bool
    root: Any

    @property
    def frames_by_path(self) -> dict[tuple[int, ...], SgfFrame]:
        return {frame.node_path: frame for frame in self.frames}

    def children_of(self, path: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
        child_depth = len(path) + 1
        children = [
            frame.node_path for frame in self.frames
            if len(frame.node_path) == child_depth and frame.node_path[:-1] == path
        ]
        return tuple(sorted(children, key=lambda child: child[-1]))


def _expanded_points(values: list[str], board_size: tuple[int, int]) -> set[tuple[int, int]]:
    points: set[tuple[int, int]] = set()
    width, height = board_size
    for raw_value in values:
        endpoints = raw_value.split(":", 1)
        try:
            first = Move.from_sgf(endpoints[0], board_size=board_size).coords
            second = Move.from_sgf(endpoints[-1], board_size=board_size).coords
        except (IndexError, ValueError):
            continue
        if first is None or second is None:
            continue
        left, right = sorted((first[0], second[0]))
        bottom, top = sorted((first[1], second[1]))
        for x in range(left, right + 1):
            for y in range(bottom, top + 1):
                if 0 <= x < width and 0 <= y < height:
                    points.add((x, y))
    return points


def _neighbors(point: tuple[int, int], board_size: tuple[int, int]):
    x, y = point
    width, height = board_size
    for adjacent in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
        if 0 <= adjacent[0] < width and 0 <= adjacent[1] < height:
            yield adjacent


def _group_and_liberties(
    board: dict[tuple[int, int], tuple[str, int | None]],
    start: tuple[int, int],
    board_size: tuple[int, int],
) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    player = board[start][0]
    group: set[tuple[int, int]] = set()
    liberties: set[tuple[int, int]] = set()
    pending = [start]
    while pending:
        point = pending.pop()
        if point in group:
            continue
        group.add(point)
        for adjacent in _neighbors(point, board_size):
            occupant = board.get(adjacent)
            if occupant is None:
                liberties.add(adjacent)
            elif occupant[0] == player and adjacent not in group:
                pending.append(adjacent)
    return group, liberties


def _play_move(
    board: dict[tuple[int, int], tuple[str, int | None]],
    point: tuple[int, int],
    player: str,
    move_number: int,
    board_size: tuple[int, int],
) -> None:
    board[point] = (player, move_number)
    opponent = "W" if player == "B" else "B"
    checked: set[tuple[int, int]] = set()
    for adjacent in _neighbors(point, board_size):
        occupant = board.get(adjacent)
        if occupant is None or occupant[0] != opponent or adjacent in checked:
            continue
        group, liberties = _group_and_liberties(board, adjacent, board_size)
        checked.update(group)
        if not liberties:
            for captured in group:
                board.pop(captured, None)

    if point in board:
        own_group, own_liberties = _group_and_liberties(board, point, board_size)
        if not own_liberties:
            for captured in own_group:
                board.pop(captured, None)


def _playback_from_root(root, start_path: list[int] | tuple[int, ...] = ()) -> SgfPlayback:
    board_size = root.board_size
    width, height = board_size
    if width < 2 or height < 2:
        raise ParseError(f"Unsupported SGF board size: {width}×{height}")

    requested_path = tuple(start_path)
    board: dict[tuple[int, int], tuple[str, int | None]] = {}
    frames: list[SgfFrame] = []
    def visit(node, node_path: tuple[int, ...], incoming_board, move_number: int,
              last_move: tuple[int, int] | None, last_player: str | None) -> None:
        current_board = dict(incoming_board)
        for point in _expanded_points(node.get_list_property("AE", []), board_size):
            current_board.pop(point, None)
        for player in ("B", "W"):
            for point in _expanded_points(node.get_list_property("A" + player, []), board_size):
                current_board[point] = (player, None)

        for move in node.moves:
            move_number += 1
            last_move = move.coords
            last_player = move.player
            if move.coords is not None:
                _play_move(current_board, move.coords, move.player, move_number, board_size)

        labels: list[tuple[int, int, str]] = []
        for raw_label in node.get_list_property("LB", []):
            if ":" not in raw_label:
                continue
            raw_point, text = raw_label.split(":", 1)
            try:
                point = Move.from_sgf(raw_point, board_size=board_size).coords
            except ValueError:
                continue
            if point is not None:
                labels.append((point[0], point[1], text))

        frames.append(SgfFrame(
            stones=tuple(
                (x, y, stone[0], stone[1])
                for (x, y), stone in sorted(current_board.items())
            ),
            move_number=move_number,
            last_move=last_move,
            last_player=last_player,
            comment=str(node.get_property("C", "") or ""),
            node_path=node_path,
            labels=tuple(labels),
            triangles=tuple(sorted(_expanded_points(node.get_list_property("TR", []), board_size))),
            circles=tuple(sorted(_expanded_points(node.get_list_property("CR", []), board_size))),
            squares=tuple(sorted(_expanded_points(node.get_list_property("SQ", []), board_size))),
            crosses=tuple(sorted(_expanded_points(node.get_list_property("MA", []), board_size))),
        ))

        for child_index, child in enumerate(node.ordered_children):
            visit(
                child, node_path + (child_index,), current_board,
                move_number, last_move, last_player,
            )

    visit(root, (), board, 0, None, None)
    frame_paths = [frame.node_path for frame in frames]
    start_path_valid = requested_path in frame_paths
    start_frame_index = frame_paths.index(requested_path) if start_path_valid else 0

    return SgfPlayback(
        board_size=board_size,
        frames=tuple(frames),
        start_frame_index=start_frame_index,
        start_path_valid=start_path_valid,
        root=root,
    )


def load_sgf_playback(path: Path, start_path: list[int] | tuple[int, ...] = ()) -> SgfPlayback:
    return _playback_from_root(GoGame.parse_file(str(path)), start_path)


def load_sgf_text(text: str, start_path: list[int] | tuple[int, ...] = ()) -> SgfPlayback:
    return _playback_from_root(GoGame.parse(text), start_path)


def _grid_geometry(
    board_rect: QRectF, board_size: tuple[int, int]
) -> tuple[float, float, float, float, float]:
    """Return (grid_left, grid_right, grid_top, grid_bottom, step) inside ``board_rect``.

    The grid is placed so that the bare wood beyond the coordinates (top/left) and
    beyond the outer edge of a corner stone (bottom/right) is identical on every side.
    """
    width, height = board_size
    side = min(board_rect.width(), board_rect.height())
    margin = max(BOARD_MARGIN_MIN, side * BOARD_MARGIN_RATIO)
    budget = max(1.0, side - 2 * margin)
    reserved = COORD_ROOM_RATIO + STONE_ROOM_RATIO
    step = min(
        budget / max(1.0, (width - 1) + reserved),
        budget / max(1.0, (height - 1) + reserved),
    )
    grid_width = step * (width - 1)
    grid_height = step * (height - 1)
    content_width = reserved * step + grid_width
    content_height = reserved * step + grid_height
    left = board_rect.left() + (board_rect.width() - content_width) / 2 + COORD_ROOM_RATIO * step
    top = board_rect.top() + (board_rect.height() - content_height) / 2 + COORD_ROOM_RATIO * step
    return left, left + grid_width, top, top + grid_height, step


def _point(left: float, bottom: float, step: float, x: int, y: int) -> QPointF:
    return QPointF(left + x * step, bottom - y * step)


def _star_points(width: int, height: int) -> list[tuple[int, int]]:
    if width != height:
        return []
    if width == 19:
        axes = (3, 9, 15)
    elif width == 13:
        axes = (3, 6, 9)
    elif width == 9:
        axes = (2, 4, 6)
    else:
        return []
    return [(x, y) for x in axes for y in axes]


def _draw_coordinates(
    painter: QPainter,
    board_rect: QRectF,
    left: float,
    right: float,
    top: float,
    bottom: float,
    step: float,
    width: int,
    height: int,
) -> None:
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(max(9, int(board_rect.width() * 0.021)))
    painter.setFont(font)
    painter.setPen(QColor("#59452b"))
    label_size = max(12.0, min(step * 0.6, board_rect.width() * 0.05))
    coordinate_gap = step * 0.54
    for x in range(width):
        label = Move.GTP_COORD[x]
        center_x = left + x * step
        painter.drawText(
            QRectF(center_x - label_size / 2, top - coordinate_gap - label_size, label_size, label_size),
            Qt.AlignCenter,
            label,
        )
    for y in range(height):
        center_y = bottom - y * step
        painter.drawText(
            QRectF(
                board_rect.left() + 2,
                center_y - label_size / 2 - 2.0,
                left - coordinate_gap - board_rect.left() - 4,
                label_size,
            ),
            Qt.AlignRight | Qt.AlignVCenter,
            str(y + 1),
        )


def _draw_markup(
    painter: QPainter,
    frame: SgfFrame,
    left: float,
    bottom: float,
    step: float,
    radius: float,
    stone_players: dict[tuple[int, int], str],
) -> None:
    blue = QColor("#318fc8")

    def marker_pen(point: tuple[int, int]) -> QPen:
        player = stone_players.get(point)
        color = QColor("#b9e5ff") if player == "B" else QColor("#176a9f") if player == "W" else blue
        return QPen(color, max(2.0, radius * 0.14), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

    marker_radius = radius * 0.54
    for point in frame.triangles:
        center = _point(left, bottom, step, *point)
        painter.setPen(marker_pen(point))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(QPolygonF([
            QPointF(center.x(), center.y() - marker_radius),
            QPointF(center.x() - marker_radius * 0.88, center.y() + marker_radius * 0.52),
            QPointF(center.x() + marker_radius * 0.88, center.y() + marker_radius * 0.52),
        ]))
    for point in frame.circles:
        center = _point(left, bottom, step, *point)
        painter.setPen(marker_pen(point))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, marker_radius, marker_radius)
    for point in frame.squares:
        center = _point(left, bottom, step, *point)
        painter.setPen(marker_pen(point))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(
            center.x() - marker_radius, center.y() - marker_radius,
            marker_radius * 2, marker_radius * 2,
        ))
    for point in frame.crosses:
        center = _point(left, bottom, step, *point)
        painter.setPen(marker_pen(point))
        offset = marker_radius * 0.78
        painter.drawLine(center + QPointF(-offset, -offset), center + QPointF(offset, offset))
        painter.drawLine(center + QPointF(-offset, offset), center + QPointF(offset, -offset))
    for x, y, label in frame.labels:
        point = (x, y)
        center = _point(left, bottom, step, x, y)
        player = stone_players.get(point)
        painter.setPen(
            QColor("#eef8ff") if player == "B" else QColor("#155f8c") if player == "W" else QColor("#174d6e")
        )
        label_font = painter.font()
        label_font.setBold(True)
        label_font.setPixelSize(max(8, int(radius * 0.76)))
        painter.setFont(label_font)
        painter.drawText(
            QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2),
            Qt.AlignCenter,
            label,
        )


def paint_sgf_board(
    painter: QPainter,
    board_rect: QRectF,
    playback: SgfPlayback,
    frame: SgfFrame,
) -> None:
    """Paint the wood, grid, coordinates, stones, and markup for ``frame``.

    Shared by the interactive editor board and the read-only search thumbnails so
    both stay visually identical.
    """
    width, height = playback.board_size
    left, right, top, bottom, step = _grid_geometry(board_rect, playback.board_size)
    side = min(board_rect.width(), board_rect.height())

    painter.setPen(QPen(QColor("#a77950"), 1))
    painter.setBrush(QColor("#d2a574"))
    painter.drawRect(board_rect)

    # Quiet vertical grain keeps the flat color warm without making the board busy.
    grain_colors = (QColor(151, 106, 72, 34), QColor(239, 210, 164, 36))
    grain_count = max(26, int(side / 12))
    for grain_index in range(grain_count):
        ratio = (grain_index + 0.35 + (grain_index % 5) * 0.11) / grain_count
        grain_x = board_rect.left() + ratio * board_rect.width()
        painter.setPen(QPen(grain_colors[grain_index % 2], 0.75))
        painter.drawLine(
            QPointF(grain_x, board_rect.top() + 1),
            QPointF(grain_x, board_rect.bottom() - 1),
        )

    painter.setPen(QPen(QColor("#46351f"), max(1.0, side / 800)))
    for x in range(width):
        px = left + x * step
        painter.drawLine(QPointF(px, top), QPointF(px, bottom))
    for row in range(height):
        py = top + row * step
        painter.drawLine(QPointF(left, py), QPointF(right, py))

    for x, y in _star_points(width, height):
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#342718"))
        painter.drawEllipse(_point(left, bottom, step, x, y), max(2.0, step * 0.09), max(2.0, step * 0.09))

    _draw_coordinates(painter, board_rect, left, right, top, bottom, step, width, height)

    radius = step * 0.46
    stone_players: dict[tuple[int, int], str] = {}
    for x, y, player, _placed_move_number in frame.stones:
        stone_players[(x, y)] = player
        center = _point(left, bottom, step, x, y)
        if player == "B":
            stone_color = QColor("#222222")
            outline = QColor("#0e0e0e")
        else:
            stone_color = QColor("#f0eee9")
            outline = QColor("#aaa7a1")
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(50, 39, 34, 42))
        painter.drawEllipse(center + QPointF(radius * 0.07, radius * 0.09), radius, radius)
        painter.setPen(QPen(outline, max(1.0, radius * 0.07)))
        painter.setBrush(stone_color)
        painter.drawEllipse(center, radius, radius)

    if frame.last_move is not None:
        x, y = frame.last_move
        center = _point(left, bottom, step, x, y)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#68b7e8"), max(2.0, radius * 0.16)))
        painter.drawEllipse(center, radius * 0.34, radius * 0.34)

    _draw_markup(painter, frame, left, bottom, step, radius, stone_players)


def render_sgf_board(
    source: Path | str,
    start_path: list[int] | tuple[int, ...] = (),
    *,
    size: int = 320,
    is_text: bool = False,
) -> QPixmap:
    """Render a read-only board thumbnail for the given SGF and start node.

    Returns a null :class:`QPixmap` when the SGF cannot be read.
    """
    try:
        playback = (
            load_sgf_text(str(source), start_path)
            if is_text
            else load_sgf_playback(Path(source), start_path)
        )
    except (OSError, ParseError, UnicodeError, ValueError, IndexError):
        return QPixmap()
    if not playback.frames:
        return QPixmap()
    path = tuple(start_path) if playback.start_path_valid else ()
    frame = playback.frames_by_path.get(path, playback.frames[0])
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor("#f6f2f1"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    paint_sgf_board(painter, QRectF(0.0, 0.0, float(size), float(size)), playback, frame)
    painter.end()
    return pixmap


class ReadOnlySgfBoard(QWidget):
    """A native Qt board with tree navigation and editable SGF markup."""

    frame_changed = Signal(int, int)
    start_requested = Signal(object)
    sgf_edited = Signal(str)
    CONTROL_PANEL_HEIGHT = 36
    OUTER_PADDING = 9

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.playback: SgfPlayback | None = None
        self.frame_index = 0
        self.active_line_paths: list[tuple[int, ...]] = []
        self.error_message = ""
        self.saved_start_path: tuple[int, ...] = ()
        self.annotation_tool: str | None = None
        self.setMinimumSize(300, 420)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)

        self.controls = QWidget(self)
        self.controls.setObjectName("sgfPlaybackControls")
        self.controls.setFixedHeight(self.CONTROL_PANEL_HEIGHT)
        self.controls.setStyleSheet(
            "QWidget#sgfPlaybackControls { background: #fffafa; "
            "border-top: 1px solid #b9cbd8; }"
        )
        controls_layout = QHBoxLayout(self.controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(0)
        self.first_button = QToolButton()
        self.first_button.setText("<<")
        self.back_ten_button = QToolButton()
        self.back_ten_button.setText("<₁₀")
        self.previous_button = QToolButton()
        self.previous_button.setText("<")
        self.next_button = QToolButton()
        self.next_button.setText(">")
        self.forward_ten_button = QToolButton()
        self.forward_ten_button.setText(">₁₀")
        self.last_button = QToolButton()
        self.last_button.setText(">>")
        self.set_start_button = QToolButton()
        self.set_start_button.setText("Set")
        self.refresh_button = QToolButton()
        self.refresh_button.setText("↻")
        self.delete_node_button = QToolButton()
        self.delete_node_button.setText("Del")
        self.delete_node_button.setEnabled(False)
        self.variation_previous_button = QToolButton()
        self.variation_previous_button.setText("V ↑")
        self.variation_next_button = QToolButton()
        self.variation_next_button.setText("V ↓")

        self.annotation_buttons: dict[str, QToolButton] = {}
        for key, text_value, tooltip in (
            ("numbers", "123", "Add or remove a numbered label"),
            ("triangles", "△", "Add or remove a triangle"),
            ("circles", "○", "Add or remove a circle"),
            ("squares", "□", "Add or remove a square"),
            ("crosses", "×", "Add or remove a cross"),
        ):
            button = QToolButton()
            button.setText(text_value)
            button.setToolTip("")
            button.setCheckable(True)
            button.setChecked(False)
            button.clicked.connect(lambda checked, name=key: self._select_annotation_tool(name, checked))
            self.annotation_buttons[key] = button

        control_buttons = [
            self.first_button, self.back_ten_button, self.previous_button,
            self.next_button, self.forward_ten_button, self.last_button,
            self.variation_previous_button, self.variation_next_button,
            *self.annotation_buttons.values(), self.delete_node_button,
            self.refresh_button, self.set_start_button,
        ]
        for button in control_buttons:
            button.setToolTip("")
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            button.setMinimumWidth(29)
            button.setAutoRaise(False)
            button.setStyleSheet(
                "QToolButton { color: #55464c; border: none; border-right: 1px solid #ddd3d6; "
                "border-bottom: 1px solid #ddd3d6; border-radius: 0; background: #fffafa; "
                "padding: 0 2px; font-size: 15px; font-weight: 700; }"
                "QToolButton:hover { background: #edf5fa; color: #356f9f; }"
                "QToolButton:checked { background: #f0d3de; color: #673548; }"
                "QToolButton:disabled { color: #b8adb1; background: #f8f4f5; }"
            )

        for key in ("squares", "crosses"):
            button = self.annotation_buttons[key]
            button.setStyleSheet(button.styleSheet() + "QToolButton { font-size: 18px; }")
        self.refresh_button.setStyleSheet(
            self.refresh_button.styleSheet() + "QToolButton { font-size: 18px; }"
        )
        self.first_button.setStyleSheet(
            self.first_button.styleSheet() + "QToolButton { border-left: 1px solid #ddd3d6; }"
        )

        controls_layout.addWidget(self.first_button)
        controls_layout.addWidget(self.back_ten_button)
        controls_layout.addWidget(self.previous_button)
        controls_layout.addWidget(self.next_button)
        controls_layout.addWidget(self.forward_ten_button)
        controls_layout.addWidget(self.last_button)
        controls_layout.addWidget(self.variation_previous_button)
        controls_layout.addWidget(self.variation_next_button)
        for button in self.annotation_buttons.values():
            controls_layout.addWidget(button)
        controls_layout.addWidget(self.delete_node_button)
        controls_layout.addWidget(self.refresh_button)
        controls_layout.addWidget(self.set_start_button)

        self.controls.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.first_button.clicked.connect(lambda: self.set_frame(0))
        self.back_ten_button.clicked.connect(lambda: self.set_frame(self.frame_index - 10))
        self.previous_button.clicked.connect(lambda: self.set_frame(self.frame_index - 1))
        self.next_button.clicked.connect(lambda: self.set_frame(self.frame_index + 1))
        self.forward_ten_button.clicked.connect(lambda: self.set_frame(self.frame_index + 10))
        self.last_button.clicked.connect(self.go_to_last)
        self.variation_previous_button.clicked.connect(lambda: self.change_variation(-1))
        self.variation_next_button.clicked.connect(lambda: self.change_variation(1))
        self.refresh_button.clicked.connect(self.refresh_start)
        self.set_start_button.clicked.connect(self._request_current_start)
        self._update_controls()
        self._position_controls()

    def load_file(self, path: Path, start_path: list[int] | tuple[int, ...] = ()) -> bool:
        try:
            self.saved_start_path = tuple(start_path)
            self.playback = load_sgf_playback(path, start_path)
            self.error_message = ""
            requested = tuple(start_path) if self.playback.start_path_valid else ()
            self._select_path(requested)
        except (OSError, ParseError, UnicodeError, ValueError, IndexError) as error:
            self.playback = None
            self.error_message = str(error)
            self.frame_index = 0
            self.active_line_paths = []
        self._update_controls()
        self.update()
        return self.playback is not None

    def load_text(self, text: str, start_path: list[int] | tuple[int, ...] = ()) -> bool:
        try:
            self.saved_start_path = tuple(start_path)
            self.playback = load_sgf_text(text, start_path)
            self.error_message = ""
            requested = tuple(start_path) if self.playback.start_path_valid else ()
            self._select_path(requested)
        except (ParseError, UnicodeError, ValueError, IndexError) as error:
            self.playback = None
            self.error_message = str(error)
            self.frame_index = 0
            self.active_line_paths = []
        self._update_controls()
        self.update()
        return self.playback is not None

    def clear(self) -> None:
        self.playback = None
        self.error_message = ""
        self.saved_start_path = ()
        self.frame_index = 0
        self.active_line_paths = []
        self._select_annotation_tool(None, False)
        self._update_controls()
        self.update()

    def _select_annotation_tool(self, tool: str | None, checked: bool) -> None:
        self.annotation_tool = tool if tool is not None and checked else None
        for name, button in self.annotation_buttons.items():
            should_be_checked = name == self.annotation_tool
            if button.isChecked() != should_be_checked:
                button.blockSignals(True)
                button.setChecked(should_be_checked)
                button.blockSignals(False)
        self.setCursor(Qt.CrossCursor if self.annotation_tool else Qt.ArrowCursor)
        self.update()

    def _node_at_path(self, path: tuple[int, ...]):
        if not self.playback:
            return None
        node = self.playback.root
        for child_index in path:
            if child_index >= len(node.ordered_children):
                return None
            node = node.ordered_children[child_index]
        return node

    @staticmethod
    def _replace_property(node, prop: str, values: list[str]) -> None:
        if values:
            node.set_property(prop, values)
        else:
            node.clear_property(prop)

    def _toggle_annotation(self, point: tuple[int, int]) -> None:
        if not self.playback or not self.current_frame or not self.annotation_tool:
            return
        node = self._node_at_path(self.current_frame.node_path)
        if node is None:
            return
        coordinate = Move(coords=point).sgf(board_size=self.playback.board_size)
        property_for_tool = {
            "triangles": "TR",
            "circles": "CR",
            "squares": "SQ",
            "crosses": "MA",
        }

        def shape_points(prop: str) -> set[tuple[int, int]]:
            return _expanded_points(node.get_list_property(prop, []), self.playback.board_size)

        def store_shape_points(prop: str, points: set[tuple[int, int]]) -> None:
            self._replace_property(node, prop, [
                Move(coords=item).sgf(board_size=self.playback.board_size)
                for item in sorted(points)
            ])

        if self.annotation_tool == "numbers":
            labels = list(node.get_list_property("LB", []))
            matching = [value for value in labels if value.split(":", 1)[0] == coordinate]
            if matching:
                labels = [value for value in labels if value.split(":", 1)[0] != coordinate]
                self._replace_property(node, "LB", labels)
            else:
                for prop in property_for_tool.values():
                    points = shape_points(prop)
                    points.discard(point)
                    store_shape_points(prop, points)
                used_numbers = {
                    int(value.split(":", 1)[1])
                    for value in labels
                    if ":" in value and value.split(":", 1)[1].isdigit()
                }
                number = 1
                while number in used_numbers:
                    number += 1
                labels.append(f"{coordinate}:{number}")
                self._replace_property(node, "LB", labels)
        else:
            selected_property = property_for_tool[self.annotation_tool]
            removing = point in shape_points(selected_property)
            for prop in property_for_tool.values():
                points = shape_points(prop)
                points.discard(point)
                store_shape_points(prop, points)
            labels = [
                value for value in node.get_list_property("LB", [])
                if value.split(":", 1)[0] != coordinate
            ]
            self._replace_property(node, "LB", labels)
            if not removing:
                points = shape_points(selected_property)
                points.add(point)
                store_shape_points(selected_property, points)

        current_path = self.current_frame.node_path
        serialized = self.playback.root.sgf()
        self.playback = _playback_from_root(self.playback.root, current_path)
        self._select_path(current_path)
        self._update_controls()
        self.update()
        self.sgf_edited.emit(serialized)

    @property
    def current_frame(self) -> SgfFrame | None:
        if self.playback and self.active_line_paths:
            return self.playback.frames_by_path[self.active_line_paths[self.frame_index]]
        return None

    def _select_path(self, path: tuple[int, ...]) -> None:
        if not self.playback:
            return
        frames = self.playback.frames_by_path
        if path not in frames:
            path = ()
        line = [path[:depth] for depth in range(len(path) + 1)]
        leaf = path
        while self.playback.children_of(leaf):
            leaf = self.playback.children_of(leaf)[0]
            line.append(leaf)
        self.active_line_paths = line
        self.frame_index = line.index(path)

    def go_to_last(self) -> None:
        if self.active_line_paths:
            self.set_frame(len(self.active_line_paths) - 1)

    def refresh_start(self) -> None:
        self._select_path(self.saved_start_path)
        self._update_controls()
        self.update()
        if self.active_line_paths:
            self.frame_changed.emit(self.frame_index, len(self.active_line_paths))

    def _variation_context(self) -> tuple[tuple[int, ...], int, int] | None:
        """Return the nearest branch parent, selected child index, and child count."""
        if not self.playback or not self.current_frame:
            return None
        path = self.current_frame.node_path
        for depth in range(len(path) - 1, -1, -1):
            parent = path[:depth]
            children = self.playback.children_of(parent)
            if len(children) > 1:
                return parent, path[depth], len(children)
        children = self.playback.children_of(path)
        if len(children) > 1:
            return path, 0, len(children)
        return None

    def change_variation(self, delta: int) -> None:
        if not self.playback or not self.current_frame:
            return
        context = self._variation_context()
        if context is None:
            return
        parent, selected, count = context
        target_depth = len(self.current_frame.node_path)
        new_path = parent + ((selected + delta) % count,)
        while len(new_path) < target_depth and self.playback.children_of(new_path):
            new_path = self.playback.children_of(new_path)[0]
        self._select_path(new_path)
        self._update_controls()
        self.update()
        self.frame_changed.emit(self.frame_index, len(self.active_line_paths))

    def set_frame(self, index: int) -> None:
        if not self.playback or not self.active_line_paths:
            return
        self.frame_index = max(0, min(index, len(self.active_line_paths) - 1))
        self._update_controls()
        self.update()
        self.frame_changed.emit(self.frame_index, len(self.active_line_paths))

    def _request_current_start(self) -> None:
        frame = self.current_frame
        if frame is None:
            return
        self.saved_start_path = frame.node_path
        self._update_controls()
        self.start_requested.emit(list(frame.node_path))

    def _update_controls(self) -> None:
        frame_count = len(self.active_line_paths)
        frame = self.current_frame
        self.previous_button.setEnabled(frame_count > 0 and self.frame_index > 0)
        self.back_ten_button.setEnabled(frame_count > 0 and self.frame_index > 0)
        self.next_button.setEnabled(frame_count > 0 and self.frame_index < frame_count - 1)
        self.forward_ten_button.setEnabled(frame_count > 0 and self.frame_index < frame_count - 1)
        self.first_button.setEnabled(frame_count > 0 and self.frame_index > 0)
        self.last_button.setEnabled(frame_count > 0 and self.frame_index < frame_count - 1)
        self.set_start_button.setEnabled(frame is not None)
        self.refresh_button.setEnabled(frame is not None)
        variation = self._variation_context()
        has_variation = variation is not None
        self.variation_previous_button.setEnabled(has_variation)
        self.variation_next_button.setEnabled(has_variation)
        if frame and self.playback:
            self.set_start_button.setText("Set ✓" if frame.node_path == self.saved_start_path else "Set")
            self.setToolTip("")
        elif self.error_message:
            self.set_start_button.setText("Set")
            self.setToolTip(self.error_message)
        else:
            self.set_start_button.setText("Set")
            self.setToolTip("")

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Left:
            self.set_frame(self.frame_index - 1)
            event.accept()
            return
        if event.key() == Qt.Key_Right:
            self.set_frame(self.frame_index + 1)
            event.accept()
            return
        if event.key() == Qt.Key_PageUp:
            self.set_frame(self.frame_index - 10)
            event.accept()
            return
        if event.key() == Qt.Key_PageDown:
            self.set_frame(self.frame_index + 10)
            event.accept()
            return
        if event.key() == Qt.Key_Up:
            self.change_variation(-1)
            event.accept()
            return
        if event.key() == Qt.Key_Down:
            self.change_variation(1)
            event.accept()
            return
        if event.key() == Qt.Key_Home:
            self.set_frame(0)
            event.accept()
            return
        if event.key() == Qt.Key_End and self.playback:
            self.go_to_last()
            event.accept()
            return
        super().keyPressEvent(event)

    def _content_rects(self) -> tuple[QRectF, QRectF, QRectF]:
        available_width = max(1, self.width() - 72)
        available_board_height = max(
            1,
            self.height() - self.CONTROL_PANEL_HEIGHT - self.OUTER_PADDING * 2 - 4,
        )
        side = float(max(1, min(available_width, available_board_height)))
        total_height = side + self.CONTROL_PANEL_HEIGHT
        left = float(round((self.width() - side) / 2))
        top = float(round((self.height() - total_height) / 2))
        board_rect = QRectF(left, top, side, side)
        panel_rect = QRectF(left, top + side, side, float(self.CONTROL_PANEL_HEIGHT))
        outer_rect = QRectF(
            left - self.OUTER_PADDING,
            top - self.OUTER_PADDING,
            side + self.OUTER_PADDING * 2,
            total_height + self.OUTER_PADDING * 2,
        )
        return outer_rect, board_rect, panel_rect

    def _position_controls(self) -> None:
        _, _, panel_rect = self._content_rects()
        self.controls.setGeometry(QRect(
            round(panel_rect.left()),
            round(panel_rect.top()),
            round(panel_rect.width()),
            round(panel_rect.height()),
        ))
        self.controls.raise_()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._position_controls()

    def _board_geometry(self) -> tuple[QRectF, float, float, float, float, float] | None:
        if not self.playback:
            return None
        _, board_rect, _ = self._content_rects()
        left, right, top, bottom, step = _grid_geometry(board_rect, self.playback.board_size)
        return board_rect, left, right, top, bottom, step

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton and self.annotation_tool and self.playback:
            geometry = self._board_geometry()
            if geometry is not None:
                _, left, _, _, bottom, step = geometry
                x = round((event.position().x() - left) / step)
                y = round((bottom - event.position().y()) / step)
                width, height = self.playback.board_size
                center = _point(left, bottom, step, x, y)
                close_enough = (
                    abs(event.position().x() - center.x()) <= step * 0.48
                    and abs(event.position().y() - center.y()) <= step * 0.48
                )
                if 0 <= x < width and 0 <= y < height and close_enough:
                    self._toggle_annotation((x, y))
                    event.accept()
                    return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f6f2f1"))
        outer_rect, media_board_rect, _ = self._content_rects()
        painter.setPen(QPen(QColor("#cfc5c8"), 1))
        painter.setBrush(QColor("#fffdfd"))
        painter.drawRoundedRect(outer_rect, 8, 8)
        if not self.playback or not self.current_frame:
            painter.setPen(QColor("#665a5e"))
            message = "Could not read SGF" if self.error_message else "No SGF"
            painter.drawText(media_board_rect, Qt.AlignCenter, message)
            return

        paint_sgf_board(painter, media_board_rect, self.playback, self.current_frame)
