import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QToolButton
from PySide6.QtTest import QTest

from go_position_db.gui import PositionImageGallery
from go_position_db.sgf_viewer import (
    COORD_ROOM_RATIO,
    STONE_ROOM_RATIO,
    ReadOnlySgfBoard,
    load_sgf_playback,
    render_sgf_board,
)


CAPTURE_SGF = """(;GM[1]FF[4]SZ[5]AB[ab][cb][ba]AW[bb]C[Initial position]
;B[bc]C[White is captured]
(;W[cc]C[Primary variation]LB[cc:1]TR[bc]CR[ab]SQ[cb]MA[ba])
(;W[dd]C[Secondary variation])
)"""


class SgfViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sgf_path = Path(self.tmp.name) / "position.sgf"
        self.sgf_path.write_text(CAPTURE_SGF, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_playback_keeps_all_variations_and_applies_captures(self):
        playback = load_sgf_playback(self.sgf_path)
        self.assertEqual(playback.board_size, (5, 5))
        self.assertEqual(len(playback.frames), 4)

        frames = playback.frames_by_path
        initial = {(x, y): player for x, y, player, _ in frames[()].stones}
        after_capture = {(x, y): player for x, y, player, _ in frames[(0,)].stones}
        primary = {(x, y): player for x, y, player, _ in frames[(0, 0)].stones}
        self.assertEqual(initial[(1, 3)], "W")
        self.assertNotIn((1, 3), after_capture)
        self.assertEqual(after_capture[(1, 2)], "B")
        self.assertEqual(primary[(2, 2)], "W")
        self.assertNotIn((3, 1), primary)
        self.assertEqual(frames[(0, 0)].comment, "Primary variation")
        self.assertEqual(frames[(0, 0)].labels, ((2, 2, "1"),))
        self.assertEqual(frames[(0, 0)].triangles, ((1, 2),))
        self.assertEqual(frames[(0, 0)].circles, ((0, 3),))
        self.assertEqual(frames[(0, 0)].squares, ((2, 3),))
        self.assertEqual(frames[(0, 0)].crosses, ((1, 4),))

        secondary_playback = load_sgf_playback(self.sgf_path, [0, 1])
        self.assertTrue(secondary_playback.start_path_valid)
        self.assertEqual(
            secondary_playback.frames[secondary_playback.start_frame_index].node_path,
            (0, 1),
        )
        secondary = secondary_playback.frames_by_path[(0, 1)]
        self.assertEqual(secondary.node_path, (0, 1))
        self.assertIn((3, 1, "W", 2), secondary.stones)

    def test_board_navigation_and_gallery_share_the_existing_media_box(self):
        board = ReadOnlySgfBoard()
        self.assertTrue(board.load_file(self.sgf_path, [0]))
        self.assertEqual(board.frame_index, 1)
        board.next_button.click()
        self.assertEqual(board.frame_index, 2)
        self.assertEqual(board.current_frame.node_path, (0, 0))
        self.assertTrue(board.variation_next_button.isEnabled())
        board.variation_next_button.click()
        self.assertEqual(board.current_frame.node_path, (0, 1))
        QTest.keyClick(board, Qt.Key_Up)
        self.assertEqual(board.current_frame.node_path, (0, 0))
        QTest.keyClick(board, Qt.Key_Down)
        self.assertEqual(board.current_frame.node_path, (0, 1))
        board.first_button.click()
        self.assertEqual(board.current_frame.node_path, ())
        board.last_button.click()
        self.assertEqual(board.current_frame.node_path, (0, 1))
        starts = []
        board.start_requested.connect(starts.append)
        board.request_current_start()
        self.assertEqual(starts, [[0, 1]])
        board.first_button.click()
        board.refresh_button.click()
        self.assertEqual(board.current_frame.node_path, (0, 1))
        board.set_frame(999)
        self.assertEqual(board.frame_index, 2)
        self.assertFalse(any(button.isChecked() for button in board.annotation_buttons.values()))
        self.assertEqual(board.controls.height(), board.CONTROL_PANEL_HEIGHT)
        outer_rect, board_rect, panel_rect = board._content_rects()
        self.assertEqual(panel_rect.left(), board_rect.left())
        self.assertEqual(panel_rect.width(), board_rect.width())
        self.assertEqual(panel_rect.top(), board_rect.bottom())
        self.assertTrue(outer_rect.contains(board_rect))
        self.assertTrue(outer_rect.contains(panel_rect))
        self.assertAlmostEqual(outer_rect.height(), board.height() - 4, delta=1)
        geometry = board._board_geometry()
        self.assertIsNotNone(geometry)
        media_rect, grid_left, grid_right, grid_top, grid_bottom, step = geometry
        self.assertGreater(grid_left - media_rect.left(), media_rect.right() - grid_right)
        self.assertGreater(grid_top - media_rect.top(), media_rect.bottom() - grid_bottom)
        # The bare wood beyond the coordinates (top/left) and beyond a corner
        # stone's outer edge (bottom/right) is the same on every side.
        wood_left = (grid_left - COORD_ROOM_RATIO * step) - media_rect.left()
        wood_top = (grid_top - COORD_ROOM_RATIO * step) - media_rect.top()
        wood_right = media_rect.right() - (grid_right + STONE_ROOM_RATIO * step)
        wood_bottom = media_rect.bottom() - (grid_bottom + STONE_ROOM_RATIO * step)
        for wood in (wood_top, wood_right, wood_bottom):
            self.assertAlmostEqual(wood_left, wood, delta=1.0)
        self.assertGreater(wood_left, 0.0)
        self.assertEqual(board.delete_node_button.text(), "Del")
        self.assertTrue(board.delete_node_button.isEnabled())
        self.assertEqual(board.annotation_buttons["numbers"].text(), "1")
        self.assertEqual(
            list(board.edit_mode_buttons),
            ["play", "black_setup", "white_setup", "erase"],
        )
        self.assertTrue(board.edit_mode_buttons["play"].isChecked())
        self.assertIn("border-left: none", board.edit_mode_buttons["play"].styleSheet())
        self.assertIn("border-right: none", board.edit_mode_buttons["erase"].styleSheet())
        self.assertIn("border-left: 2px solid #9fc2d8", board.variation_previous_button.styleSheet())
        self.assertIn("border-right: none", board.refresh_button.styleSheet())
        self.assertIn("border-left", board.first_button.styleSheet())
        self.assertTrue(all(
            "border-bottom" in button.styleSheet()
            for button in board.controls.findChildren(QToolButton)
        ))
        self.assertTrue(all(not button.toolTip() for button in board.controls.findChildren(QToolButton)))
        self.assertEqual(
            [
                board.first_button.text(), board.back_ten_button.text(),
                board.previous_button.text(), board.next_button.text(),
                board.forward_ten_button.text(), board.last_button.text(),
            ],
            ["<<", "<₁₀", "<", ">", ">₁₀", ">>"],
        )

        gallery = PositionImageGallery()
        gallery.resize(700, 620)
        gallery.set_images([
            ("Main image", QPixmap(QSize(20, 20))),
            ("Solution 1", QPixmap(QSize(20, 20))),
        ], self.sgf_path)
        gallery.show()
        self.app.processEvents()
        self.assertIs(gallery.media_stack.currentWidget(), gallery.sgf_board)
        self.assertEqual(gallery.sgf_board.size(), gallery.media_stack.contentsRect().size())

        gallery.select_image(1)
        self.app.processEvents()
        self.assertIs(gallery.media_stack.currentWidget(), gallery.image_surface)
        self.assertEqual(gallery.image_surface.size(), gallery.media_stack.contentsRect().size())
        self.assertEqual(
            gallery.image_surface._content_rects(),
            gallery.sgf_board._content_rects(),
        )
        gallery.close()

    def test_annotation_tools_edit_current_node_and_emit_updated_sgf(self):
        board = ReadOnlySgfBoard()
        self.assertTrue(board.load_file(self.sgf_path, [0, 1]))
        original_text = self.sgf_path.read_text(encoding="utf-8")
        edits = []
        board.sgf_edited.connect(edits.append)

        board.annotation_buttons["triangles"].click()
        self.assertEqual(board.annotation_tool, "triangles")
        board._toggle_annotation((4, 4))
        self.assertIn((4, 4), board.current_frame.triangles)
        self.assertTrue(edits)
        self.assertEqual(self.sgf_path.read_text(encoding="utf-8"), original_text)

        board.annotation_buttons["numbers"].click()
        self.assertEqual(board.annotation_tool, "numbers")
        self.assertFalse(board.annotation_buttons["triangles"].isChecked())
        board._toggle_annotation((0, 0))
        board._toggle_annotation((1, 0))
        self.assertIn((0, 0, "1"), board.current_frame.labels)
        self.assertIn((1, 0, "2"), board.current_frame.labels)

        board.annotation_buttons["letters"].click()
        board._toggle_annotation((2, 0))
        board._toggle_annotation((3, 0))
        self.assertIn((2, 0, "A"), board.current_frame.labels)
        self.assertIn((3, 0, "B"), board.current_frame.labels)

        reloaded = ReadOnlySgfBoard()
        self.assertTrue(reloaded.load_text(edits[-1], [0, 1]))
        self.assertIn((1, 0, "2"), reloaded.current_frame.labels)

    def test_play_setup_erase_and_side_to_move_edit_the_sgf(self):
        board = ReadOnlySgfBoard()
        self.assertTrue(board.load_file(self.sgf_path))
        edits = []
        board.sgf_edited.connect(edits.append)

        # Clicking the already-selected play tool explicitly changes whose turn it is.
        self.assertEqual(board._current_player_to_move(), "B")
        board.edit_mode_buttons["play"].click()
        self.assertEqual(board._current_player_to_move(), "W")
        self.assertEqual(board.edit_mode_buttons["play"].player, "W")

        board.edit_mode_buttons["black_setup"].click()
        board._edit_board_point((1, 3))
        stones = {(x, y): player for x, y, player, _number in board.current_frame.stones}
        self.assertEqual(stones[(1, 3)], "B")

        board.edit_mode_buttons["white_setup"].click()
        board._edit_board_point((1, 3))
        stones = {(x, y): player for x, y, player, _number in board.current_frame.stones}
        self.assertEqual(stones[(1, 3)], "W")

        board.edit_mode_buttons["erase"].click()
        board._edit_board_point((1, 3))
        stones = {(x, y): player for x, y, player, _number in board.current_frame.stones}
        self.assertNotIn((1, 3), stones)

        board.edit_mode_buttons["play"].click()
        board._edit_board_point((4, 4))
        self.assertEqual(board.current_frame.last_move, (4, 4))
        self.assertEqual(board.current_frame.last_player, "W")
        self.assertTrue(board.delete_node_button.isEnabled())
        board.delete_current_node(confirm=False)
        self.assertEqual(board.current_frame.node_path, ())
        self.assertFalse(board.delete_node_button.isEnabled())
        self.assertTrue(edits)

    def test_render_sgf_board_thumbnail_for_search_results(self):
        thumb = render_sgf_board(self.sgf_path, [0, 0], size=200)
        self.assertFalse(thumb.isNull())
        self.assertEqual(thumb.size(), QSize(200, 200))

        from_text = render_sgf_board(CAPTURE_SGF, [0], size=160, is_text=True)
        self.assertFalse(from_text.isNull())
        self.assertEqual(from_text.size(), QSize(160, 160))

        self.assertTrue(render_sgf_board(Path(self.tmp.name) / "missing.sgf").isNull())
        bad = Path(self.tmp.name) / "bad.sgf"
        bad.write_text("not an sgf", encoding="utf-8")
        self.assertTrue(render_sgf_board(bad).isNull())

    def test_invalid_sgf_is_reported_without_crashing(self):
        self.sgf_path.write_text("not an sgf", encoding="utf-8")
        board = ReadOnlySgfBoard()
        self.assertFalse(board.load_file(self.sgf_path))
        self.assertIsNone(board.playback)
        self.assertTrue(board.error_message)
        self.assertFalse(board.refresh_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
