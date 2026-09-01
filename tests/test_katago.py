import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QProcess

from go_position_db.config import KataGoConfig, load_config, save_katago_config
from go_position_db.katago import (
    ANALYSIS_REPORT_INTERVAL_SECONDS,
    CONTINUOUS_ANALYSIS_MAX_VISITS,
    KataGoClient,
    KataGoError,
    build_analysis_query,
    parse_analysis_response,
    validate_katago_config,
)
from go_position_db.sgf_viewer import load_sgf_text


class FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class FakeKataGoProcess:
    def __init__(self):
        self.started = FakeSignal()
        self.readyReadStandardOutput = FakeSignal()
        self.readyReadStandardError = FakeSignal()
        self.errorOccurred = FakeSignal()
        self.finished = FakeSignal()
        self._state = QProcess.ProcessState.NotRunning
        self._stdout = b""
        self._stderr = b""
        self.program = ""
        self.arguments = []
        self.working_directory = ""
        self.writes = []
        self.killed = False

    def state(self):
        return self._state

    def setProgram(self, program):
        self.program = program

    def setArguments(self, arguments):
        self.arguments = list(arguments)

    def setWorkingDirectory(self, directory):
        self.working_directory = directory

    def start(self):
        self._state = QProcess.ProcessState.Running
        self.started.emit()

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def readAllStandardOutput(self):
        value, self._stdout = self._stdout, b""
        return value

    def readAllStandardError(self):
        value, self._stderr = self._stderr, b""
        return value

    def feed_stdout(self, payload):
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        self._stdout += payload.encode("utf-8") + b"\n"
        self.readyReadStandardOutput.emit()

    def errorString(self):
        return "fake process error"

    def closeWriteChannel(self):
        self._state = QProcess.ProcessState.NotRunning

    def waitForFinished(self, _milliseconds):
        return self._state == QProcess.ProcessState.NotRunning

    def kill(self):
        self.killed = True
        self._state = QProcess.ProcessState.NotRunning


class KataGoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.executable = root / ("katago.exe" if os.name == "nt" else "katago")
        self.model = root / "model.bin.gz"
        self.analysis_config = root / "analysis.cfg"
        for path in (self.executable, self.model, self.analysis_config):
            path.write_bytes(b"fixture")
        if os.name != "nt":
            self.executable.chmod(0o755)
        self.config = KataGoConfig(
            executable=self.executable,
            model=self.model,
            analysis_config=self.analysis_config,
            timeout_seconds=2,
            startup_timeout_seconds=5,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_configuration_loads_relative_paths_and_reports_missing_files(self):
        root = Path(self.tmp.name)
        database_root = root / "database"
        app_root = root / "application"
        database_root.mkdir()
        app_root.mkdir()
        executable = app_root / self.executable.name
        model = app_root / self.model.name
        analysis_config = app_root / self.analysis_config.name
        self.executable.replace(executable)
        self.model.replace(model)
        self.analysis_config.replace(analysis_config)
        app_config = app_root / "config.yaml"
        app_config.write_text(
            "katago:\n"
            "  executable: katago" + (".exe" if os.name == "nt" else "") + "\n"
            "  model: model.bin.gz\n"
            "  analysis_config: analysis.cfg\n"
            "  timeout_seconds: 4.5\n"
            "  startup_timeout_seconds: 90\n",
            encoding="utf-8",
        )
        loaded = load_config(database_root, config_path=app_config)
        self.assertEqual(loaded.root, database_root)
        self.assertEqual(loaded.positions_dir, database_root / "positions")
        self.assertEqual(loaded.katago.executable, executable)
        self.assertEqual(loaded.katago.timeout_seconds, 4.5)
        self.assertEqual(loaded.katago.startup_timeout_seconds, 90)
        validate_katago_config(loaded.katago)

        with self.assertRaisesRegex(KataGoError, "does not exist"):
            validate_katago_config(KataGoConfig(
                executable=root / "missing",
                model=model,
                analysis_config=analysis_config,
            ))

    def test_query_represents_the_exact_displayed_variation(self):
        playback = load_sgf_text(
            "(;GM[1]FF[4]SZ[9]KM[7.5]AB[aa]PL[W];W[bb](;B[cc])(;B[dd]))",
            [0, 1],
        )
        frame = playback.frames_by_path[(0, 1)]
        query = build_analysis_query(frame, playback.board_size, "W", komi=7.5)
        self.assertEqual(query["boardXSize"], 9)
        self.assertEqual(query["initialPlayer"], "W")
        self.assertEqual(query["moves"], [])
        self.assertEqual(
            set(map(tuple, query["initialStones"])),
            {("B", "A9"), ("W", "B8"), ("B", "D6")},
        )
        self.assertTrue(query["includeOwnership"])
        self.assertEqual(query["maxVisits"], CONTINUOUS_ANALYSIS_MAX_VISITS)
        self.assertEqual(
            query["reportDuringSearchEvery"], ANALYSIS_REPORT_INTERVAL_SECONDS
        )
        self.assertEqual(query["rootPolicyTemperature"], 1.0)

    def test_fixture_response_exposes_root_candidates_and_ownership(self):
        response = {
            "id": "request-1",
            "isDuringSearch": False,
            "rootInfo": {
                "currentPlayer": "W", "winrate": 0.625,
                "scoreLead": 2.75, "visits": 50,
            },
            "moveInfos": [
                {"move": "D4", "order": 0, "visits": 31, "winrate": 0.64, "scoreLead": 3.1},
                {"move": "E5", "order": 1, "visits": 12, "winrate": 0.60, "scoreLead": 2.2},
            ],
            "ownership": [0.25] * 81,
        }
        result = parse_analysis_response(response, (9, 9))
        self.assertEqual(result.current_player, "W")
        self.assertEqual(result.visits, 50)
        self.assertEqual(result.candidates[0].move, "D4")
        self.assertEqual(len(result.candidates), 2)
        self.assertAlmostEqual(result.candidates[0].point_loss, 0.0)
        self.assertAlmostEqual(result.candidates[1].point_loss, 0.9)
        self.assertEqual(len(result.ownership or ()), 81)

        malformed = dict(response, ownership=[0.2])
        with self.assertRaisesRegex(KataGoError, "ownership"):
            parse_analysis_response(malformed, (9, 9))

    def test_fake_process_start_replacement_and_stale_response_invalidation(self):
        process = FakeKataGoProcess()
        client = KataGoClient(self.config, process=process)
        results = []
        failures = []
        client.analysis_ready.connect(results.append)
        client.analysis_failed.connect(failures.append)
        query = {
            "initialStones": [], "moves": [], "initialPlayer": "B",
            "rules": "japanese", "komi": 6.5,
            "boardXSize": 9, "boardYSize": 9,
        }

        first_id = client.analyze(query)
        self.assertEqual(process.program, str(self.executable))
        self.assertEqual(process.arguments[:2], ["analysis", "-config"])
        override_index = process.arguments.index("-override-config") + 1
        self.assertIn("numAnalysisThreads=1", process.arguments[override_index])
        self.assertIn(
            "numSearchThreadsPerAnalysisThread=16",
            process.arguments[override_index],
        )
        self.assertIn("nnCacheSizePowerOfTwo=20", process.arguments[override_index])
        self.assertIn("reportAnalysisWinratesAs=BLACK", process.arguments[override_index])
        self.assertEqual(process.working_directory, str(self.analysis_config.parent))
        sent = [json.loads(line) for line in process.writes]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["action"], "query_version")
        process.feed_stdout({
            "id": sent[0]["id"], "action": "query_version",
            "version": "1.18.1", "git_hash": "fixture",
        })
        second_id = client.analyze(query)
        sent = [json.loads(line) for line in process.writes]
        self.assertEqual(sent[2]["action"], "terminate")
        self.assertEqual(sent[2]["terminateId"], first_id)
        self.assertEqual(sent[3]["id"], second_id)

        response = {
            "isDuringSearch": False,
            "rootInfo": {"currentPlayer": "B", "visits": 10, "winrate": 0.5},
            "moveInfos": [{"move": "D4", "visits": 10, "scoreLead": 1.0}],
        }
        process.feed_stdout(dict(response, id=first_id))
        self.assertEqual(results, [])
        process.feed_stdout(dict(response, id=second_id))
        self.assertEqual(len(results), 1)
        self.assertEqual(failures, [])
        self.assertIsNone(client.active_request_id)

    def test_engine_can_warm_start_without_an_analysis_request(self):
        process = FakeKataGoProcess()
        client = KataGoClient(self.config, process=process)
        ready = []
        busy = []
        client.engine_ready.connect(ready.append)
        client.busy_changed.connect(busy.append)

        client.start()

        self.assertEqual(process.program, str(self.executable))
        sent = [json.loads(line) for line in process.writes]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["action"], "query_version")
        self.assertIsNone(client.active_request_id)
        self.assertEqual(busy, [])

        process.feed_stdout({
            "id": sent[0]["id"], "action": "query_version",
            "version": "1.18.1", "git_hash": "fixture",
        })
        self.assertEqual(ready, ["1.18.1"])
        self.assertEqual(len(process.writes), 1)

    def test_fake_process_emits_repeated_progress_until_final_or_cancelled(self):
        process = FakeKataGoProcess()
        client = KataGoClient(self.config, process=process)
        progress = []
        completed = []
        client.analysis_progress.connect(progress.append)
        client.analysis_ready.connect(completed.append)
        request_id = client.analyze({
            "initialStones": [], "moves": [], "initialPlayer": "B",
            "rules": "japanese", "komi": 6.5,
            "boardXSize": 9, "boardYSize": 9,
        })
        readiness = json.loads(process.writes[0])
        process.feed_stdout({
            "id": readiness["id"], "action": "query_version",
            "version": "1.18.1", "git_hash": "fixture",
        })

        def response(visits, during_search):
            return {
                "id": request_id,
                "isDuringSearch": during_search,
                "rootInfo": {
                    "currentPlayer": "B", "visits": visits,
                    "winrate": 0.55, "scoreLead": 1.2,
                },
                "moveInfos": [
                    {"move": "D4", "order": 0, "visits": visits - 2, "scoreLead": 1.2},
                    {"move": "Q16", "order": 1, "visits": 2, "scoreLead": -0.3},
                ],
            }

        process.feed_stdout(response(10, True))
        process.feed_stdout(response(20, True))
        self.assertEqual([item.visits for item in progress], [10, 20])
        self.assertTrue(all(not item.is_final for item in progress))
        self.assertEqual(len(progress[-1].candidates), 2)
        self.assertAlmostEqual(progress[-1].candidates[1].point_loss, 1.5)
        self.assertEqual(client.active_request_id, request_id)
        self.assertEqual(completed, [])

        process.feed_stdout(response(25, False))
        self.assertEqual(len(completed), 1)
        self.assertTrue(completed[0].is_final)
        self.assertIsNone(client.active_request_id)

    def test_save_katago_config_preserves_other_application_settings(self):
        root = Path(self.tmp.name)
        application = root / "application"
        application.mkdir()
        config_path = application / "config.yaml"
        config_path.write_text(
            "positions_directory: custom-positions\n"
            "katago:\n"
            "  max_visits: 99\n",
            encoding="utf-8",
        )
        save_katago_config(self.config, config_path)
        text = config_path.read_text(encoding="utf-8")
        self.assertIn("positions_directory: custom-positions", text)
        self.assertNotIn("max_visits", text)
        loaded = load_config(root / "database", config_path=config_path)
        self.assertEqual(loaded.katago.executable, self.executable)
        self.assertEqual(loaded.katago.model, self.model)
        self.assertEqual(loaded.katago.analysis_config, self.analysis_config)

    def test_fake_process_malformed_response_is_understandable(self):
        process = FakeKataGoProcess()
        client = KataGoClient(self.config, process=process)
        failures = []
        client.analysis_failed.connect(failures.append)
        client.analyze({
            "initialStones": [], "moves": [], "initialPlayer": "B",
            "rules": "japanese", "komi": 6.5,
            "boardXSize": 9, "boardYSize": 9,
        })
        readiness = json.loads(process.writes[0])
        process.feed_stdout({
            "id": readiness["id"], "action": "query_version",
            "version": "1.18.1", "git_hash": "fixture",
        })
        process.feed_stdout("not-json")
        self.assertEqual(failures, ["KataGo returned malformed JSON."])

    def test_invalid_readiness_response_stops_the_unusable_process(self):
        process = FakeKataGoProcess()
        client = KataGoClient(self.config, process=process)
        failures = []
        client.analysis_failed.connect(failures.append)
        client.analyze({
            "initialStones": [], "moves": [], "initialPlayer": "B",
            "rules": "japanese", "komi": 6.5,
            "boardXSize": 9, "boardYSize": 9,
        })
        readiness = json.loads(process.writes[0])
        process.feed_stdout({"id": readiness["id"], "action": "query_version"})
        self.assertEqual(failures, ["KataGo returned an invalid readiness response."])
        self.assertTrue(process.killed)


if __name__ == "__main__":
    unittest.main()
