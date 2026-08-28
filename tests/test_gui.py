import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit

from go_position_db.config import Config
from go_position_db.gui import (
    DISPLAY_MODES,
    ElidedLabel,
    PositionEditor,
    PositionImageGallery,
    SearchResultCard,
    TagChipDisplay,
    TagQueryLineEdit,
    TagSetEditor,
    TagManagerPage,
    MainWindow,
    score_chip_stylesheet,
)
from go_position_db.storage import atomic_dump_yaml, load_position, save_position
from go_position_db.tags import TagGraph


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config.defaults(Path(self.tmp.name))
        self.cfg.positions_dir.mkdir(parents=True)
        self.cfg.index_file.parent.mkdir(parents=True)
        atomic_dump_yaml(self.cfg.tags_file, {"tags": {"joseki": {"parents": []}}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_gallery_shows_one_image_and_navigates(self):
        gallery = PositionImageGallery()
        gallery.set_images([
            ("Main image", QPixmap(QSize(20, 20))),
            ("Solution 1", QPixmap(QSize(20, 20))),
        ])
        self.assertFalse(gallery.previous_button.isEnabled())
        self.assertTrue(gallery.next_button.isEnabled())
        gallery.select_image(1)
        self.assertEqual(gallery.selected_index, 1)
        self.assertIn("Solution 1", gallery.position_label.text())
        self.assertTrue(gallery.previous_button.isEnabled())
        self.assertFalse(gallery.next_button.isEnabled())

    def test_tag_filter_selects_creates_and_description_autosaves(self):
        manager = TagManagerPage(self.cfg)
        manager.filter_edit.setText("joseki")
        manager.select_or_create_filter_tag()
        self.assertEqual(manager._current_tag(), "joseki")
        manager.tag_description_edit.setPlainText("Corner opening sequences")
        self.assertTrue(manager.flush_description_save())
        self.assertEqual(TagGraph(self.cfg).info("joseki").description, "Corner opening sequences")

        manager.filter_edit.setText("New_Tag")
        manager.select_or_create_filter_tag()
        self.assertEqual(manager._current_tag(), "new-tag")
        self.assertTrue(TagGraph(self.cfg).has("new-tag"))

    def test_untouched_new_position_is_discarded(self):
        position_id = "p000001"
        (self.cfg.positions_dir / position_id).mkdir()
        save_position(self.cfg, position_id, {
            "description": "",
            "score": "",
            "tags": [],
            "metadata": {},
            "solution_images": [],
        })
        editor = PositionEditor(self.cfg)
        self.assertTrue(editor.load_position(position_id))
        editor.transient_new_position = True
        self.assertTrue(editor.discard_empty_new_position())
        self.assertFalse((self.cfg.positions_dir / position_id).exists())

    def test_detailed_card_omits_empty_fields_and_splits_metadata_rows(self):
        empty = SearchResultCard("p1", {}, None, DISPLAY_MODES["Detailed"])
        texts = [label.text() for label in empty.findChildren(QLabel)]
        self.assertIn("p1", texts)
        self.assertNotIn("(no description)", texts)
        self.assertEqual(empty.findChildren(QPlainTextEdit), [])

        populated = SearchResultCard(
            "p2",
            {"metadata": {"game": "Kitani vs Seigen", "move": 38}},
            None,
            DISPLAY_MODES["Detailed"],
        )
        texts = [label.text() for label in populated.findChildren(QLabel)]
        self.assertIn("game", texts)
        self.assertIn("Kitani vs Seigen", texts)
        self.assertIn("move", texts)
        self.assertIn("38", texts)

    def test_search_card_prefers_rendered_sgf_over_image(self):
        sgf_path = Path(self.tmp.name) / "position.sgf"
        sgf_path.write_text("(;GM[1]FF[4]SZ[9];B[ee];W[gc])", encoding="utf-8")
        card = SearchResultCard(
            "p1", {"sgf_start_path": [0]}, None,
            DISPLAY_MODES["Standard"], sgf_path=sgf_path,
        )
        self.assertEqual(card.sgf_start_path, [0])
        self.assertFalse(card.image_label.pixmap().isNull())
        self.assertEqual(card.image_label.text(), "")

        image_path = Path(self.tmp.name) / "board.png"
        image = QPixmap(QSize(20, 20))
        image.fill(QColor("#ff0000"))
        self.assertTrue(image.save(str(image_path)))
        image_card = SearchResultCard(
            "p1", {"main_media_kind": "image", "sgf_start_path": [0]}, image_path,
            DISPLAY_MODES["Standard"], sgf_path=sgf_path,
        )
        preview = image_card.image_label.pixmap().toImage()
        self.assertGreater(preview.pixelColor(preview.width() // 2, preview.height() // 2).red(), 240)

        missing = SearchResultCard(
            "p2", {}, None, DISPLAY_MODES["Standard"],
            sgf_path=Path(self.tmp.name) / "absent.sgf",
        )
        self.assertEqual(missing.image_label.text(), "No image")

    def test_position_tag_editor_creates_tags_and_keeps_only_descendants(self):
        atomic_dump_yaml(self.cfg.tags_file, {
            "tags": {
                "joseki": {"parents": []},
                "corner-joseki": {"parents": ["joseki"]},
                "3-3-joseki": {"parents": ["corner-joseki"]},
            }
        })
        editor = TagSetEditor(self.cfg)
        editor.set_available_tags(TagGraph(self.cfg).names())
        editor.set_tags(["joseki"])
        editor.add_edit.setText("3-3-joseki")
        editor.add_from_edit()
        self.assertEqual(editor.tags(), ["3-3-joseki"])

        with patch("go_position_db.gui.QMessageBox.information"):
            editor.add_edit.setText("corner-joseki")
            editor.add_from_edit()
        self.assertEqual(editor.tags(), ["3-3-joseki"])

        with patch("go_position_db.gui.QMessageBox.information"):
            editor.add_edit.setText("fresh_tag")
            editor.add_from_edit()
        self.assertIn("fresh-tag", editor.tags())
        self.assertTrue(TagGraph(self.cfg).has("fresh-tag"))

    def test_standard_view_uses_three_columns(self):
        self.assertEqual(DISPLAY_MODES["Standard"].columns, 3)

    def test_colored_query_tags_preserve_normal_editing_and_score_variants(self):
        query = TagQueryLineEdit()
        query.set_boolean_query_mode()
        self.assertEqual(query.textMargins().left(), 10)
        query.resize(500, 42)
        query.set_tag_names(["joseki", "sente", "reverse-sente"])
        query.show()
        query.setFocus()
        QTest.keyClicks(query, "joseki")
        self.assertEqual(len(query._tag_matches(query.text())), 1)
        QTest.keyClick(query, Qt.Key_Backspace)
        self.assertEqual(query.text(), "josek")
        self.assertEqual(query._tag_matches(query.text()), [])
        QTest.keyClicks(query, "i and not sente")
        self.assertEqual(query.text(), "joseki and not sente")
        self.assertEqual(len(query._tag_matches(query.text())), 2)
        self.assertEqual(query.cursorPosition(), len(query.text()))
        searches = []
        query.returnPressed.connect(lambda: searches.append(query.text()))
        QTest.keyClick(query, Qt.Key_Return)
        self.assertEqual(searches, [query.text()])
        self.app.processEvents()
        self.assertFalse(query.grab().isNull())

        tags = TagChipDisplay(["joseki", "reverse-sente"], centered=True)
        self.assertEqual(tags.height(), 56)
        white_score_style = score_chip_stylesheet("W +2.5")
        black_score_style = score_chip_stylesheet("B +2.5")
        self.assertIn("background: white", white_score_style)
        self.assertIn("color: #356f9f", white_score_style)
        self.assertIn("background: #171717", black_score_style)
        self.assertIn("color: #8dc8f2", black_score_style)

        editor = PositionEditor(self.cfg)
        self.assertEqual(editor.score_edit.placeholderText(), "")
        editor.score_edit.setText("-4.5")
        editor._normalize_score_input()
        self.assertEqual(editor.score_edit.text(), "W +4.5")
        editor.score_edit.setText("3")
        editor._normalize_score_input()
        self.assertEqual(editor.score_edit.text(), "B +3")

        self.assertTrue(editor.score_edit.isHidden())
        editor.score_edit.setText("-2.75")
        editor._commit_score_input()
        self.app.processEvents()
        self.assertEqual(editor.score_edit.text(), "W +2.75")
        self.assertIn("border-radius: 18px", editor.score_edit.styleSheet())
        editor.close()

        description = ElidedLabel("A long description " * 100, max_lines=3)
        description.resize(260, 200)
        description._refresh_text()
        self.assertTrue(description.text().endswith("…"))

    def test_startup_maintenance_normalizes_sgf_name(self):
        position_id = "p000001"
        directory = self.cfg.positions_dir / position_id
        directory.mkdir()
        (directory / "downloaded-game.sgf").write_text("(;GM[1])", encoding="utf-8")
        (directory / self.cfg.image_filename).write_bytes(b"image")
        save_position(self.cfg, position_id, {
            "description": "",
            "score": "",
            "tags": [],
            "metadata": {},
            "solution_images": [],
        })
        window = MainWindow(self.cfg.root)
        self.assertTrue((directory / self.cfg.sgf_filename).exists())
        self.assertFalse((directory / "downloaded-game.sgf").exists())
        self.assertIs(window.editor.save_status_label.parentWidget(), window.statusBar())
        window.close()

    def test_sgf_markup_edits_are_saved_to_the_position_copy(self):
        position_id = "p000001"
        directory = self.cfg.positions_dir / position_id
        directory.mkdir()
        sgf_path = directory / self.cfg.sgf_filename
        sgf_path.write_text("(;GM[1]FF[4]SZ[9];B[ee])", encoding="utf-8")
        save_position(self.cfg, position_id, {
            "description": "",
            "score": "",
            "tags": [],
            "metadata": {},
            "solution_images": [],
        })

        editor = PositionEditor(self.cfg)
        self.assertTrue(editor.load_position(position_id))
        self.assertTrue(editor.score_edit.isHidden())
        board = editor.image_gallery.sgf_board
        board.annotation_buttons["triangles"].click()
        board._toggle_annotation((0, 0))
        self.assertIsNotNone(editor.pending_sgf_text)
        self.assertNotIn("TR[ai]", sgf_path.read_text(encoding="utf-8"))

        board.next_button.click()
        editor.add_solution_board()
        self.assertEqual(editor.selected_image_index, 1)
        self.assertEqual(editor.solution_images[0]["kind"], "board")
        self.assertEqual(editor.solution_images[0]["sgf_start_path"], [0])
        self.assertIs(editor.image_gallery.media_stack.currentWidget(), board)
        self.assertEqual(editor.baseline_menu_button.text(), "Set baseline")
        self.assertEqual(editor.baseline_menu_button.toolTip(), "Selected baseline: board")
        baseline_actions = {
            action.text(): action
            for action in editor.baseline_menu_button.menu().actions()
            if action.text()
        }
        self.assertEqual(
            set(baseline_actions),
            {
                "Image from file…", "Image from clipboard", "Current SGF node",
                "Display SGF board", "Display image",
            },
        )
        self.assertTrue(baseline_actions["Display SGF board"].isChecked())
        self.assertFalse(baseline_actions["Display image"].isEnabled())
        baseline_actions["Current SGF node"].trigger()
        self.assertEqual(editor.solution_images[0]["sgf_start_path"], [0])

        solution_image = QPixmap(QSize(20, 20))
        solution_image.fill(QColor("#ff0000"))
        solution_file = "solutions/solution-001.png"
        editor.solution_images[0]["file"] = solution_file
        editor.pending_solution_sources[solution_file] = solution_image.toImage()
        editor.refresh_gallery(1)
        baseline_actions = {
            action.text(): action
            for action in editor.baseline_menu_button.menu().actions()
            if action.text()
        }
        self.assertTrue(baseline_actions["Display image"].isEnabled())
        baseline_actions["Display image"].trigger()
        self.assertEqual(editor.solution_images[0]["kind"], "image")
        self.assertIs(
            editor.image_gallery.media_stack.currentWidget(),
            editor.image_gallery.image_surface,
        )
        baseline_actions = {
            action.text(): action
            for action in editor.baseline_menu_button.menu().actions()
            if action.text()
        }
        baseline_actions["Display SGF board"].trigger()
        self.assertEqual(editor.solution_images[0]["kind"], "board")
        self.assertEqual(len(editor.solution_tab_buttons), 2)
        self.assertEqual(editor.solution_tab_buttons[0].text(), "Main")
        self.assertEqual(editor.solution_tab_buttons[1].text(), "S1")
        editor.resize(1500, 900)
        editor.show()
        self.app.processEvents()
        solution_controls_geometry = editor.solution_controls.geometry()
        initial_strip_left = editor.solution_strip.geometry().left()
        initial_strip_width = editor.solution_strip.width()
        initial_baseline_left = editor.baseline_menu_button.geometry().left()
        self.assertEqual(editor.solution_strip.height(), 40)
        self.assertTrue(all(button.height() == 34 for button in editor.solution_tab_buttons))
        self.assertEqual(editor.sgf_menu_btn.size(), editor.open_folder_btn.size())
        self.assertEqual(editor.open_folder_btn.size(), editor.delete_btn.size())
        self.assertEqual(editor.back_btn.height(), editor.sgf_menu_btn.height())
        self.assertEqual(editor.baseline_menu_button.height(), editor.sgf_menu_btn.height())
        self.assertEqual(editor.solution_delete_button.height(), editor.sgf_menu_btn.height())
        editor.refresh_gallery(0)
        self.app.processEvents()
        self.assertEqual(editor.solution_controls.geometry(), solution_controls_geometry)
        self.assertEqual(editor.solution_delete_button.text(), "")
        self.assertFalse(editor.solution_delete_button.isEnabled())
        editor.refresh_gallery(1)
        self.app.processEvents()
        self.assertEqual(editor.solution_controls.geometry(), solution_controls_geometry)
        self.assertEqual(editor.solution_delete_button.text(), "Del")
        self.assertTrue(editor.save_current())
        self.assertIn("TR[ai]", sgf_path.read_text(encoding="utf-8"))
        saved = load_position(self.cfg, position_id)
        self.assertEqual(saved["solution_images"][0]["kind"], "board")
        self.assertEqual(saved["solution_images"][0]["sgf_start_path"], [0])

        # Main plus five solutions fit before the overflow menu is needed.
        for _ in range(4):
            editor.add_solution_board()
        self.app.processEvents()
        self.assertEqual(
            [button.text() for button in editor.solution_tab_buttons],
            ["Main", "S1", "S2", "S3", "S4", "S5"],
        )
        self.assertEqual(editor.solution_strip.geometry().left(), initial_strip_left)
        self.assertGreater(editor.solution_strip.width(), initial_strip_width)
        self.assertLessEqual(editor.solution_strip.width(), editor.solution_strip_slot.width())
        self.assertEqual(editor.baseline_menu_button.geometry().left(), initial_baseline_left)


if __name__ == "__main__":
    unittest.main()
