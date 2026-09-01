from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .config import KataGoConfig
from .sgf_viewer import SgfFrame


class KataGoError(RuntimeError):
    pass


@dataclass(frozen=True)
class KataGoCandidate:
    move: str
    order: int
    visits: int
    winrate: float | None = None
    score_lead: float = 0.0
    point_loss: float = 0.0


@dataclass(frozen=True)
class KataGoAnalysis:
    request_id: str
    current_player: str | None
    winrate: float | None
    score_lead: float | None
    visits: int
    candidates: tuple[KataGoCandidate, ...]
    ownership: tuple[float, ...] | None = None
    is_final: bool = True


CONTINUOUS_ANALYSIS_MAX_VISITS = 2_000_000_000
ANALYSIS_REPORT_INTERVAL_SECONDS = 0.1


def validate_katago_config(config: KataGoConfig) -> None:
    missing = []
    for label, value in (
        ("executable", config.executable),
        ("model", config.model),
        ("analysis configuration", config.analysis_config),
    ):
        if value is None:
            missing.append(label)
    if missing:
        raise KataGoError(
            "KataGo is not configured. Set katago.executable, katago.model, and "
            "katago.analysis_config in config.yaml."
        )

    assert config.executable is not None
    assert config.model is not None
    assert config.analysis_config is not None
    for label, path in (
        ("KataGo executable", config.executable),
        ("KataGo model", config.model),
        ("KataGo analysis configuration", config.analysis_config),
    ):
        if not path.exists():
            raise KataGoError(f"{label} does not exist: {path}")
        if not path.is_file():
            raise KataGoError(f"{label} is not a file: {path}")
    if os.name != "nt" and not os.access(config.executable, os.X_OK):
        raise KataGoError(f"KataGo executable is not executable: {config.executable}")
    if config.timeout_seconds <= 0:
        raise KataGoError("KataGo timeout_seconds must be positive.")
    if config.startup_timeout_seconds <= 0:
        raise KataGoError("KataGo startup_timeout_seconds must be positive.")
    if not 0.1 <= config.report_interval_seconds <= 10:
        raise KataGoError(
            "KataGo report_interval_seconds must be from 0.1 through 10."
        )
    if not 1 <= config.overlay_top_moves <= 50:
        raise KataGoError("KataGo overlay_top_moves must be from 1 through 50.")
    if not 0 <= config.overlay_max_point_loss <= 100:
        raise KataGoError(
            "KataGo overlay_max_point_loss must be from 0 through 100."
        )
    if not 0.1 <= config.root_policy_temperature <= 10:
        raise KataGoError(
            "KataGo root_policy_temperature must be from 0.1 through 10."
        )
    if not 1 <= config.num_analysis_threads <= 64:
        raise KataGoError("KataGo num_analysis_threads must be from 1 through 64.")
    if not 1 <= config.num_search_threads <= 512:
        raise KataGoError("KataGo num_search_threads must be from 1 through 512.")
    if not 10 <= config.nn_cache_size_power_of_two <= 30:
        raise KataGoError(
            "KataGo nn_cache_size_power_of_two must be from 10 through 30."
        )


def _gtp_point(x: int, y: int) -> str:
    # GTP columns skip I and use the same extended coordinate sequence as pysgf.
    from pysgf import Move

    return f"{Move.GTP_COORD[x]}{y + 1}"


def build_analysis_query(
    frame: SgfFrame,
    board_size: tuple[int, int],
    player_to_move: str,
    *,
    rules: str = "japanese",
    komi: float = 6.5,
    root_policy_temperature: float = 1.1,
    report_interval_seconds: float = ANALYSIS_REPORT_INTERVAL_SECONDS,
    request_id: str = "pending",
) -> dict[str, Any]:
    """Build a query for exactly the displayed board, without inventing history."""
    width, height = board_size
    if player_to_move not in {"B", "W"}:
        raise KataGoError("The displayed SGF does not identify a valid player to move.")
    stones = [
        [player, _gtp_point(x, y)]
        for x, y, player, _move_number in frame.stones
    ]
    return {
        "id": request_id,
        "initialStones": stones,
        "moves": [],
        "initialPlayer": player_to_move,
        "rules": rules,
        "komi": komi,
        "boardXSize": width,
        "boardYSize": height,
        # KataGo requires a finite limit, so use an effectively unreachable one
        # for interactive analysis and terminate explicitly on navigation/Stop.
        "maxVisits": CONTINUOUS_ANALYSIS_MAX_VISITS,
        "reportDuringSearchEvery": report_interval_seconds,
        "rootPolicyTemperature": root_policy_temperature,
        "includeOwnership": True,
        "analysisPVLen": 8,
    }


def parse_analysis_response(payload: Any, board_size: tuple[int, int]) -> KataGoAnalysis:
    if not isinstance(payload, dict):
        raise KataGoError("KataGo returned a response that was not a JSON object.")
    request_id = payload.get("id")
    if not isinstance(request_id, str):
        raise KataGoError("KataGo returned a response without a request id.")
    if payload.get("noResults"):
        raise KataGoError("KataGo stopped before producing analysis results.")
    root = payload.get("rootInfo")
    moves = payload.get("moveInfos")
    if not isinstance(root, dict) or not isinstance(moves, list):
        raise KataGoError("KataGo returned an incomplete analysis response.")

    def optional_number(value: Any, label: str) -> float | None:
        if value is None:
            return None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise KataGoError(f"KataGo returned an invalid {label}.")
        return float(value)

    current_player = root.get("currentPlayer")
    if current_player is not None and current_player not in {"B", "W"}:
        raise KataGoError("KataGo returned an invalid current player.")

    candidate_values: list[tuple[str, int, int, float | None, float]] = []
    for item in moves:
        if not isinstance(item, dict) or not isinstance(item.get("move"), str):
            raise KataGoError("KataGo returned a malformed candidate move.")
        visits = item.get("visits", 0)
        if not isinstance(visits, int) or isinstance(visits, bool) or visits < 0:
            raise KataGoError("KataGo returned an invalid candidate visit count.")
        order = item.get("order", len(candidate_values))
        if not isinstance(order, int) or isinstance(order, bool) or order < 0:
            raise KataGoError("KataGo returned an invalid candidate order.")
        score_lead = optional_number(item.get("scoreLead"), "candidate score estimate")
        if score_lead is None:
            raise KataGoError("KataGo returned a candidate without a score estimate.")
        candidate_values.append((
            item["move"], order, visits,
            optional_number(item.get("winrate"), "candidate win rate"),
            score_lead,
        ))

    candidate_values.sort(key=lambda value: value[1])
    best_score = candidate_values[0][4] if candidate_values else 0.0
    candidates = tuple(
        KataGoCandidate(
            move=move,
            order=order,
            visits=visits,
            winrate=winrate,
            score_lead=score_lead,
            # Candidate zero is KataGo's selected best move. The reporting
            # perspective (BLACK, WHITE, or side-to-move) is configurable in
            # analysis.cfg, so use the magnitude of the score difference.
            point_loss=abs(best_score - score_lead),
        )
        for move, order, visits, winrate, score_lead in candidate_values
    )

    ownership_value = payload.get("ownership")
    ownership: tuple[float, ...] | None = None
    if ownership_value is not None:
        expected = board_size[0] * board_size[1]
        if (
            not isinstance(ownership_value, list)
            or len(ownership_value) != expected
            or any(
                not isinstance(value, (int, float)) or isinstance(value, bool)
                for value in ownership_value
            )
        ):
            raise KataGoError("KataGo returned malformed ownership data.")
        ownership = tuple(float(value) for value in ownership_value)

    visits = root.get("visits", 0)
    if not isinstance(visits, int) or isinstance(visits, bool) or visits < 0:
        raise KataGoError("KataGo returned an invalid root visit count.")
    return KataGoAnalysis(
        request_id=request_id,
        current_player=current_player,
        winrate=optional_number(root.get("winrate"), "root win rate"),
        score_lead=optional_number(root.get("scoreLead"), "root score estimate"),
        visits=visits,
        candidates=candidates,
        ownership=ownership,
        is_final=payload.get("isDuringSearch") is not True,
    )


class KataGoClient(QObject):
    """Own one asynchronous KataGo analysis process for the application session."""

    analysis_ready = Signal(object)
    analysis_progress = Signal(object)
    analysis_failed = Signal(str)
    busy_changed = Signal(bool)
    engine_ready = Signal(str)
    engine_failed = Signal(str)

    def __init__(self, config: KataGoConfig, parent: QObject | None = None, process=None):
        super().__init__(parent)
        self.config = config
        self.process = process if process is not None else QProcess(self)
        self._stdout = ""
        self._stderr = ""
        self._pending_query: dict[str, Any] | None = None
        self._active_id: str | None = None
        self._active_board_size: tuple[int, int] | None = None
        self._ready = False
        self._readiness_id: str | None = None
        self._closing = False
        self._startup_timeout = QTimer(self)
        self._startup_timeout.setSingleShot(True)
        self._startup_timeout.timeout.connect(self._on_startup_timeout)
        self._analysis_timeout = QTimer(self)
        self._analysis_timeout.setSingleShot(True)
        self._analysis_timeout.timeout.connect(self._on_analysis_timeout)
        self.process.started.connect(self._begin_readiness_check)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.errorOccurred.connect(self._process_error)
        self.process.finished.connect(self._process_finished)

    @property
    def active_request_id(self) -> str | None:
        return self._active_id

    def start(self) -> None:
        """Warm the engine process and model without submitting analysis."""
        validate_katago_config(self.config)
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        assert self.config.executable is not None
        assert self.config.model is not None
        assert self.config.analysis_config is not None
        self.process.setProgram(str(self.config.executable))
        self.process.setArguments([
            "analysis",
            "-config", str(self.config.analysis_config),
            "-model", str(self.config.model),
            "-override-config",
            ",".join((
                f"numAnalysisThreads={self.config.num_analysis_threads}",
                "numSearchThreadsPerAnalysisThread="
                f"{self.config.num_search_threads}",
                "nnCacheSizePowerOfTwo="
                f"{self.config.nn_cache_size_power_of_two}",
                # The application stores score leads as B+/W+, so make score
                # and win-rate perspectives deterministic.
                "reportAnalysisWinratesAs=BLACK",
            )),
            "-quit-without-waiting",
        ])
        # Relative log and backend-cache paths in KataGo's config should be
        # kept with the engine, never in the application's source checkout.
        if hasattr(self.process, "setWorkingDirectory"):
            self.process.setWorkingDirectory(str(self.config.analysis_config.parent))
        self.process.start()

    def analyze(self, query: dict[str, Any]) -> str:
        validate_katago_config(self.config)
        self.cancel()
        request_id = uuid4().hex
        query = dict(query)
        query["id"] = request_id
        self._active_id = request_id
        self._active_board_size = (int(query["boardXSize"]), int(query["boardYSize"]))
        self._pending_query = query
        self.busy_changed.emit(True)
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self.start()
        elif self.process.state() == QProcess.ProcessState.Running:
            if self._ready:
                self._send_pending()
        return request_id

    def _begin_readiness_check(self) -> None:
        if self._closing:
            return
        self._ready = False
        self._readiness_id = f"readiness-{uuid4().hex}"
        try:
            self._write({"id": self._readiness_id, "action": "query_version"})
        except KataGoError as error:
            self._fail_startup(str(error))
            return
        self._startup_timeout.start(
            max(1, round(self.config.startup_timeout_seconds * 1000))
        )

    def _write(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        if self.process.write(line.encode("utf-8")) < 0:
            raise KataGoError("Could not send the analysis request to KataGo.")

    def _send_pending(self) -> None:
        if self._pending_query is None or self._closing:
            return
        query = self._pending_query
        self._pending_query = None
        try:
            self._write(query)
        except KataGoError as error:
            self._fail(str(error))
            return
        self._analysis_timeout.start(max(1, round(self.config.timeout_seconds * 1000)))

    def cancel(self) -> None:
        old_id = self._active_id
        self._pending_query = None
        self._active_id = None
        self._active_board_size = None
        self._analysis_timeout.stop()
        if old_id and self.process.state() == QProcess.ProcessState.Running:
            try:
                self._write({"id": f"terminate-{uuid4().hex}", "action": "terminate", "terminateId": old_id})
            except KataGoError:
                pass
        if old_id:
            self.busy_changed.emit(False)

    def _read_stdout(self) -> None:
        self._stdout += bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self._stdout:
            line, self._stdout = self._stdout.split("\n", 1)
            if line.strip():
                self._handle_line(line)

    def _read_stderr(self) -> None:
        chunk = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        self._stderr = (self._stderr + chunk)[-4000:]

    def _handle_line(self, line: str) -> None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            if self._active_id:
                message = "KataGo returned malformed JSON."
                if self._ready:
                    self._fail(message)
                else:
                    self._fail_startup(message)
            return
        if not isinstance(payload, dict):
            if self._active_id:
                message = "KataGo returned a response that was not a JSON object."
                if self._ready:
                    self._fail(message)
                else:
                    self._fail_startup(message)
            return
        if payload.get("id") == self._readiness_id:
            if "error" in payload:
                self._fail_startup(f"KataGo readiness check failed: {payload['error']}")
                return
            if payload.get("action") != "query_version" or not isinstance(payload.get("version"), str):
                self._fail_startup("KataGo returned an invalid readiness response.")
                return
            self._startup_timeout.stop()
            self._readiness_id = None
            self._ready = True
            self.engine_ready.emit(payload["version"])
            self._send_pending()
            return
        if payload.get("id") != self._active_id:
            return
        if "error" in payload:
            field = f" ({payload['field']})" if payload.get("field") else ""
            self._fail(f"KataGo rejected the request{field}: {payload['error']}")
            return
        if "warning" in payload:
            return
        try:
            assert self._active_board_size is not None
            result = parse_analysis_response(payload, self._active_board_size)
        except KataGoError as error:
            self._fail(str(error))
            return
        self._analysis_timeout.start(max(1, round(self.config.timeout_seconds * 1000)))
        if not result.is_final:
            self.analysis_progress.emit(result)
            return
        self._analysis_timeout.stop()
        self._active_id = None
        self._active_board_size = None
        self.busy_changed.emit(False)
        self.analysis_ready.emit(result)

    def _on_startup_timeout(self) -> None:
        if self._ready or self._closing:
            return
        self._fail_startup(
            f"KataGo startup timed out after {self.config.startup_timeout_seconds:g} seconds."
        )

    def _on_analysis_timeout(self) -> None:
        if self._active_id:
            self.cancel()
            self.analysis_failed.emit(
                f"KataGo analysis timed out after {self.config.timeout_seconds:g} seconds."
            )

    def _process_error(self, _error) -> None:
        if self._closing:
            return
        self._ready = False
        self._readiness_id = None
        self._startup_timeout.stop()
        detail = self.process.errorString()
        self._fail_startup(f"KataGo could not be started or contacted: {detail}")

    def _process_finished(self, exit_code: int, _status) -> None:
        was_ready = self._ready
        self._ready = False
        self._readiness_id = None
        self._startup_timeout.stop()
        if self._closing:
            return
        if self._active_id or self._pending_query is not None:
            detail = self._stderr.strip().splitlines()[-1] if self._stderr.strip() else "no error output"
            self._fail(f"KataGo exited unexpectedly (code {exit_code}): {detail}")
        elif was_ready:
            self.engine_failed.emit(f"KataGo exited unexpectedly (code {exit_code}).")

    def _fail(self, message: str) -> None:
        had_request = self._active_id is not None or self._pending_query is not None
        self._active_id = None
        self._active_board_size = None
        self._pending_query = None
        self._startup_timeout.stop()
        self._analysis_timeout.stop()
        if had_request:
            self.busy_changed.emit(False)
            self.analysis_failed.emit(message)

    def _fail_startup(self, message: str) -> None:
        self._ready = False
        self._readiness_id = None
        self.engine_failed.emit(message)
        self._fail(message)
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def shutdown(self, wait_ms: int = 1200) -> None:
        self._closing = True
        self.cancel()
        self._startup_timeout.stop()
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.process.closeWriteChannel()
        if not self.process.waitForFinished(wait_ms):
            self.process.kill()
            self.process.waitForFinished(500)
