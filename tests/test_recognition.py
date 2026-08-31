from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from go_position_db.recognition import (
    ExternalRecognitionService,
    LIZGOBAN_ASSET_DIR,
    RecognitionError,
    RecognitionResult,
    RecognitionUnavailableError,
    recognition_result_from_sgf,
    recognition_status,
)


class RecognitionTests(unittest.TestCase):
    def test_result_rejects_malformed_or_contradictory_points(self):
        with self.assertRaises(RecognitionError):
            RecognitionResult.from_payload({"version": 1, "board_size": 19, "black": [[0]], "white": []})
        with self.assertRaises(RecognitionError):
            RecognitionResult.from_payload({
                "version": 1, "board_size": 19,
                "black": [[3, 3]], "white": [[3, 3]],
            })
        with self.assertRaises(RecognitionError):
            RecognitionResult.from_payload({
                "version": 1, "board_size": 19,
                "black": [[19, 0]], "white": [],
            })

    def test_external_service_rejects_failure_and_malformed_json(self):
        service = ExternalRecognitionService()
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "recognizer.exe"
            executable.touch()
            image = Path(temporary) / "image.png"
            image.touch()
            with patch.dict("os.environ", {"GO_POSITION_DB_RECOGNIZER": str(executable)}), patch(
                "go_position_db.recognition.subprocess.run"
            ) as run:
                run.return_value.returncode = 1
                run.return_value.stderr = "grid not found\n"
                with self.assertRaisesRegex(RecognitionError, "grid not found"):
                    service.recognize(image)
                run.return_value.returncode = 0
                run.return_value.stdout = "not json"
                with self.assertRaisesRegex(RecognitionError, "malformed JSON"):
                    service.recognize(image)

    def test_missing_custom_recognizer_is_rejected_by_external_service(self):
        with patch.dict(
            "os.environ",
            {"GO_POSITION_DB_RECOGNIZER": str(Path(tempfile.gettempdir()) / "missing-recognizer")},
            clear=False,
        ):
            with self.assertRaises(RecognitionUnavailableError):
                ExternalRecognitionService()._command(Path("image.png"), 19)

    def test_builtin_lizgoban_provider_is_always_available(self):
        with patch.dict("os.environ", {}, clear=True):
            available, message = recognition_status()
            self.assertTrue(available)
            self.assertIn("Built-in LizGoban", message)
            with self.assertRaises(RecognitionUnavailableError):
                ExternalRecognitionService()._command(Path("image.png"), 19)

    def test_reviewed_lizgoban_sgf_becomes_setup_stones(self):
        result = recognition_result_from_sgf("(;SZ[19]PL[W]KM[7.5]AB[aa][ss]AW[jj])")
        self.assertEqual(result.board_size, 19)
        self.assertEqual(result.black, frozenset({(0, 18), (18, 0)}))
        self.assertEqual(result.white, frozenset({(9, 9)}))
        self.assertEqual(result.player_to_move, "W")
        self.assertEqual(result.komi, 7.5)

    def test_reviewed_lizgoban_sgf_rejects_malformed_or_nonstatic_results(self):
        for value in (
            "", "not sgf", "(;SZ[x]PL[B]KM[6.5]AB[aa])",
            "(;SZ[19]PL[B]KM[6.5];B[aa])",
            "(;SZ[19]PL[B]KM[6.5]AB[aa]AW[aa])",
            "(;SZ[19]KM[6.5]AB[aa])", "(;SZ[19]PL[X]KM[6.5]AB[aa])",
            "(;SZ[19]PL[B]AB[aa])", "(;SZ[19]PL[B]KM[6.25]AB[aa])",
        ):
            with self.subTest(value=value), self.assertRaises(RecognitionError):
                recognition_result_from_sgf(value)

    def test_pinned_lizgoban_assets_are_self_contained(self):
        html = (LIZGOBAN_ASSET_DIR / "sgf_from_image.html").read_text(encoding="utf-8")
        javascript = (LIZGOBAN_ASSET_DIR / "sgf_from_image.js").read_text(encoding="utf-8")
        upstream = (LIZGOBAN_ASSET_DIR / "UPSTREAM.md").read_text(encoding="utf-8")
        self.assertNotIn("node_modules", html)
        self.assertNotIn("twgl.", javascript)
        self.assertNotIn("<h1>SGF from Image</h1>", html)
        self.assertNotIn("copy_to_clipboard", html)
        self.assertNotIn('addEventListener("paste"', javascript)
        self.assertNotIn("Q('#ok').disabled", javascript)
        self.assertIn("ok && (ok.disabled", javascript)
        self.assertIn("clientWidth * 0.84", javascript)
        self.assertIn("clientHeight * 0.60", javascript)
        self.assertIn('<ol id="steps">', html)
        self.assertIn('id="komi" value="6.5"', html)
        self.assertIn('KM[${komi}]', javascript)
        self.assertIn("20944051392479082d7c54793917d3150bc6e01d", upstream)
        self.assertTrue((LIZGOBAN_ASSET_DIR / "LICENSE.txt").is_file())
        self.assertTrue((Path(__file__).parents[1] / "LICENSE").is_file())


if __name__ == "__main__":
    unittest.main()
