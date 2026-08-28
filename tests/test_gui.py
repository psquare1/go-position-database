import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtGui import QPixmap
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
from go_position_db.storage import atomic_dump_yaml, save_position
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

    def test_visual_chips_use_query_and_score_variants(self):
        query = TagQueryLineEdit()
        query.resize(500, 42)
        query.setText("joseki AND reverse-sente")
        query.show()
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

        editor.setEnabled(True)
        editor.show()
        editor.score_edit.setFocus()
        self.app.processEvents()
        self.assertTrue(editor.score_edit.hasFocus())
        self.assertEqual(editor.score_edit.styleSheet(), "")
        editor.score_edit.setText("-2.75")
        editor._commit_score_input()
        self.app.processEvents()
        self.assertEqual(editor.score_edit.text(), "W +2.75")
        self.assertFalse(editor.score_edit.hasFocus())
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
        window.close()


if __name__ == "__main__":
    unittest.main()
