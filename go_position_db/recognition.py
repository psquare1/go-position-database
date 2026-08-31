from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Any

from pysgf import GoGame, Move, ParseError


LIZGOBAN_ASSET_DIR = Path(__file__).parent / "assets" / "lizgoban_sgf_from_image"
LIZGOBAN_HTML = LIZGOBAN_ASSET_DIR / "sgf_from_image.html"


class RecognitionError(RuntimeError):
    """A recognition failure that is safe to present to the user."""


class RecognitionUnavailableError(RecognitionError):
    pass


def _point(value: Any, label: str, board_size: int) -> tuple[int, int]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or not all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        raise RecognitionError(f"{label} must contain [x, y] integer coordinates.")
    point = (value[0], value[1])
    if not all(0 <= coordinate < board_size for coordinate in point):
        raise RecognitionError(f"{label} coordinate {point} is outside a {board_size}x{board_size} board.")
    return point


def _points(value: Any, label: str, board_size: int) -> frozenset[tuple[int, int]]:
    if not isinstance(value, list):
        raise RecognitionError(f"{label} must be a list of coordinates.")
    points = [_point(item, label, board_size) for item in value]
    if len(points) != len(set(points)):
        raise RecognitionError(f"{label} contains duplicate coordinates.")
    return frozenset(points)


@dataclass(frozen=True)
class RecognitionResult:
    board_size: int
    black: frozenset[tuple[int, int]]
    white: frozenset[tuple[int, int]]
    player_to_move: str = "B"
    komi: float = 6.5
    uncertain: frozenset[tuple[int, int]] = frozenset()
    warnings: tuple[str, ...] = ()
    diagnostics: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.board_size, int) or isinstance(self.board_size, bool) or not 2 <= self.board_size <= 52:
            raise RecognitionError("Recognized board size must be an integer from 2 through 52.")
        for label, points in (("Black stones", self.black), ("White stones", self.white), ("Uncertain intersections", self.uncertain)):
            for point in points:
                _point(point, label, self.board_size)
        overlap = self.black & self.white
        if overlap:
            raise RecognitionError(f"Recognition assigned both colors to {sorted(overlap)[0]}.")
        if self.player_to_move not in {"B", "W"}:
            raise RecognitionError("Player to move must be B or W.")
        if (
            not isinstance(self.komi, (int, float))
            or isinstance(self.komi, bool)
            or not math.isfinite(self.komi)
            or not -400 <= self.komi <= 400
            or not float(self.komi * 2).is_integer()
        ):
            raise RecognitionError("Komi must be an integer or half-integer from -400 through 400.")
        if not all(isinstance(item, str) for item in self.warnings):
            raise RecognitionError("Recognition warnings must be text.")
        if self.diagnostics is not None and not isinstance(self.diagnostics, dict):
            raise RecognitionError("Recognition diagnostics must be an object.")

    @classmethod
    def from_payload(cls, payload: Any) -> "RecognitionResult":
        if not isinstance(payload, dict):
            raise RecognitionError("Recognizer output must be a JSON object.")
        if payload.get("version") != 1:
            raise RecognitionError("Recognizer output uses an unsupported protocol version.")
        board_size = payload.get("board_size")
        if not isinstance(board_size, int) or isinstance(board_size, bool):
            raise RecognitionError("Recognizer output is missing a valid board_size.")
        warnings = payload.get("warnings", [])
        if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
            raise RecognitionError("Recognizer warnings must be a list of text messages.")
        return cls(
            board_size=board_size,
            black=_points(payload.get("black"), "Black stones", board_size),
            white=_points(payload.get("white"), "White stones", board_size),
            player_to_move=payload.get("player_to_move", "B"),
            komi=payload.get("komi", 6.5),
            uncertain=_points(payload.get("uncertain", []), "Uncertain intersections", board_size),
            warnings=tuple(warnings),
            diagnostics=payload.get("diagnostics", {}),
        )


def _setup_points(values: list[str], board_size: int, label: str) -> frozenset[tuple[int, int]]:
    points: set[tuple[int, int]] = set()
    dimensions = (board_size, board_size)
    for value in values:
        endpoints = value.split(":", 1)
        try:
            first = Move.from_sgf(endpoints[0], board_size=dimensions).coords
            last = Move.from_sgf(endpoints[-1], board_size=dimensions).coords
        except (IndexError, TypeError, ValueError) as exc:
            raise RecognitionError(f"The reviewed SGF contains an invalid {label} coordinate.") from exc
        if first is None or last is None:
            raise RecognitionError(f"The reviewed SGF contains an invalid {label} coordinate.")
        x1, x2 = sorted((first[0], last[0]))
        y1, y2 = sorted((first[1], last[1]))
        points.update((x, y) for x in range(x1, x2 + 1) for y in range(y1, y2 + 1))
    return frozenset(points)


def recognition_result_from_sgf(sgf_text: str) -> RecognitionResult:
    """Validate the reviewed LizGoban setup SGF and convert it to app coordinates."""
    if not isinstance(sgf_text, str) or not sgf_text.strip():
        raise RecognitionError(
            "No detected position is available yet. Finish the three calibration steps first."
        )
    try:
        root = GoGame.parse(sgf_text).root
    except (ParseError, TypeError, ValueError) as exc:
        raise RecognitionError("The image converter returned malformed SGF.") from exc
    if root.ordered_children or root.get_list_property("B", []) or root.get_list_property("W", []):
        raise RecognitionError("The reviewed result must be a static setup position, not a move sequence.")
    raw_size = str(root.get_property("SZ", "19"))
    dimensions = raw_size.split(":", 1)
    try:
        width, height = (int(dimensions[0]), int(dimensions[-1]))
    except ValueError as exc:
        raise RecognitionError("The reviewed SGF has an invalid board size.") from exc
    if width != height:
        raise RecognitionError("Only square recognized boards can be converted.")
    player_to_move = str(root.get_property("PL", "")).strip().upper()
    if player_to_move not in {"B", "W"}:
        raise RecognitionError("The reviewed SGF is missing a valid player to move.")
    try:
        komi = float(root.get_property("KM"))
    except (TypeError, ValueError) as exc:
        raise RecognitionError("The reviewed SGF is missing a valid komi.") from exc
    black = _setup_points(root.get_list_property("AB", []), width, "black-stone")
    white = _setup_points(root.get_list_property("AW", []), width, "white-stone")
    return RecognitionResult(width, black, white, player_to_move, komi)


def recognition_status() -> tuple[bool, str]:
    required = (LIZGOBAN_HTML, LIZGOBAN_ASSET_DIR / "sgf_from_image.js", LIZGOBAN_ASSET_DIR / "perspective.js")
    available = all(path.is_file() for path in required)
    message = "Built-in LizGoban image-to-SGF converter is available."
    return available, message if available else "Built-in LizGoban image-to-SGF assets are missing."


class ExternalRecognitionService:
    def __init__(self, *, timeout: float = 60.0):
        self.timeout = timeout

    def _command(self, image_path: Path, board_size: int) -> list[str]:
        custom = os.environ.get("GO_POSITION_DB_RECOGNIZER")
        if custom:
            executable = Path(custom).expanduser()
            if not executable.exists():
                raise RecognitionUnavailableError(f"Configured recognizer does not exist: {executable}")
            return [str(executable), "--input", str(image_path), "--board-size", str(board_size)]
        raise RecognitionUnavailableError(
            "No external image-recognition provider is configured."
        )

    def recognize(self, image_path: Path, *, board_size: int = 19) -> RecognitionResult:
        try:
            completed = subprocess.run(
                self._command(image_path, board_size),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RecognitionError("Image recognition timed out. The original image was not changed.") from exc
        except OSError as exc:
            raise RecognitionError(f"Could not start the image recognizer: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()
            message = detail[-1] if detail else "The external recognizer failed without an explanation."
            raise RecognitionError(f"Image recognition failed: {message}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RecognitionError("The external recognizer returned malformed JSON.") from exc
        return RecognitionResult.from_payload(payload)
