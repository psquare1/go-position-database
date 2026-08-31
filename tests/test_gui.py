import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPixmap, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPlainTextEdit

from go_position_db.config import Config
from go_position_db.database import GoPositionDatabase
from go_position_db.dialogs import SilentMessageBox as QMessageBox
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
from go_position_db.recognition import (
    RecognitionError,
    RecognitionResult,
    RecognitionUnavailableError,
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

    def test_gallery_has_no_carousel_controls(self):
        gallery = PositionImageGallery()
        gallery.set_images([
            ("Primary position", QPixmap(QSize(20, 20))),
            ("Variation 1", QPixmap(QSize(20, 20))),
        ])
        self.assertFalse(hasattr(gallery, "previous_button"))
        self.assertFalse(hasattr(gallery, "next_button"))
        gallery.select_image(1)
        self.assertEqual(gallery.selected_index, 1)
        self.assertIs(gallery.media_stack.currentWidget(), gallery.image_surface)

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

    def test_tag_manager_delete_refreshes_and_removes_position_references(self):
        position_id = "p000010"
        directory = self.cfg.positions_dir / position_id
        directory.mkdir()
        save_position(self.cfg, position_id, {
            "description": "",
            "score": "",
            "tags": ["joseki"],
            "metadata": {},
            "solution_images": [],
        })
        manager = TagManagerPage(self.cfg)
        manager.refresh()
        item = manager.tag_list.findItems("joseki", Qt.MatchExactly)[0]
        manager.tag_list.setCurrentItem(item)

        with patch(
            "go_position_db.gui.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ) as confirmation:
            manager.delete_tag()

            self.assertIn("removed from 1 directly tagged entry", confirmation.call_args.args[2])
        self.assertFalse(TagGraph(self.cfg).has("joseki"))
        self.assertEqual(load_position(self.cfg, position_id)["tags"], [])
        self.assertFalse(manager.tag_list.findItems("joseki", Qt.MatchExactly))
        self.assertIsNone(manager.loaded_tag_name)

        # The same index operation used by New Position must remain valid.
        GoPositionDatabase(self.cfg).rebuild_index()

    def test_tag_manager_deletes_unused_tag_without_confirmation(self):
        graph = TagGraph(self.cfg)
        graph.add("unused-tag")
        GoPositionDatabase(self.cfg).rebuild_index()
        manager = TagManagerPage(self.cfg)
        manager.refresh()
        item = manager.tag_list.findItems("unused-tag", Qt.MatchExactly)[0]
        manager.tag_list.setCurrentItem(item)

        with patch("go_position_db.gui.QMessageBox.question") as confirmation:
            manager.delete_tag()

        confirmation.assert_not_called()
        self.assertFalse(TagGraph(self.cfg).has("unused-tag"))

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

        with patch(
            "go_position_db.gui.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ) as confirm_create:
            editor.add_edit.setText("fresh_tag")
            editor.add_from_edit()
        confirm_create.assert_called_once()
        self.assertIn("fresh-tag", editor.tags())
        self.assertTrue(TagGraph(self.cfg).has("fresh-tag"))

        with patch(
            "go_position_db.gui.QMessageBox.question",
            return_value=QMessageBox.Cancel,
        ):
            editor.add_edit.setText("not-created")
            editor.add_from_edit()
        self.assertNotIn("not-created", editor.tags())
        self.assertFalse(TagGraph(self.cfg).has("not-created"))

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
        query_image = query.grab().toImage()
        self.assertFalse(query_image.isNull())
        # Recognized tags are painted once in rose. The previous overlay approach
        # left a second, dark copy of each glyph underneath and offset from it.
        neutral_dark_pixels = 0
        rose_pixels = 0
        for y in range(5, query_image.height() - 5):
            for x in range(5, min(180, query_image.width() - 5)):
                color = query_image.pixelColor(x, y)
                if max(color.red(), color.green(), color.blue()) < 180:
                    if max(color.red(), color.green(), color.blue()) - min(
                        color.red(), color.green(), color.blue()
                    ) < 18:
                        neutral_dark_pixels += 1
                    elif color.red() > color.green() + 25:
                        rose_pixels += 1
        self.assertGreater(rose_pixels, 0)
        self.assertLess(neutral_dark_pixels, 250)

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
        self.assertEqual(window.browse_nav_btn.text(), "Browse entries")
        self.assertEqual(window.new_nav_btn.text(), "New entry")
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
        self.assertEqual(editor.baseline_menu_button.text(), "Set starting view")
        self.assertEqual(editor.baseline_menu_button.toolTip(), "Selected starting view: board")
        baseline_actions = {
            action.text(): action
            for action in editor.baseline_menu_button.menu().actions()
            if action.text()
        }
        self.assertEqual(
            set(baseline_actions),
            {
                "Set image", "Set SGF", "Set current SGF node",
                "Display SGF board", "Display Image", "Del",
            },
        )
        self.assertEqual(
            {action.text() for action in baseline_actions["Set image"].menu().actions()},
            {"From files…", "From clipboard"},
        )
        self.assertEqual(
            {action.text() for action in baseline_actions["Set SGF"].menu().actions()},
            {"New SGF", "From files…", "From clipboard", "From selected image"},
        )
        delete_solution_action = baseline_actions["Del"]
        self.assertTrue(delete_solution_action.isEnabled())
        self.assertIn("primary position is not deleted", delete_solution_action.toolTip())
        self.assertIn("background: #f6c7cf", delete_solution_action.defaultWidget().styleSheet())
        self.assertGreaterEqual(
            sum(action.isSeparator() for action in editor.baseline_menu_button.menu().actions()),
            3,
        )
        self.assertTrue(baseline_actions["Display SGF board"].isChecked())
        self.assertFalse(baseline_actions["Display Image"].isEnabled())
        baseline_actions["Set current SGF node"].trigger()
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
        self.assertTrue(baseline_actions["Display Image"].isEnabled())
        baseline_actions["Display Image"].trigger()
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
        self.assertEqual(editor.solution_tab_buttons[0].text(), "Primary")
        self.assertEqual(editor.solution_tab_buttons[1].text(), "V1")
        editor.resize(1500, 900)
        editor.show()
        self.app.processEvents()
        self.assertEqual(editor.image_panel.geometry().top(), editor.details_panel.geometry().top())
        self.assertEqual(editor.image_panel.geometry().bottom(), editor.details_panel.geometry().bottom())
        self.assertGreater(editor.image_panel.width(), editor.details_panel.width())
        board_outer, _board_media, _board_controls = (
            editor.image_gallery.sgf_board._content_rects()
        )
        self.assertLessEqual(board_outer.top(), 3)
        self.assertLessEqual(
            editor.image_gallery.sgf_board.height() - board_outer.bottom(), 3
        )
        solution_controls_geometry = editor.solution_controls.geometry()
        initial_strip_left = editor.solution_strip.geometry().left()
        initial_strip_width = editor.solution_strip.width()
        initial_baseline_left = editor.baseline_menu_button.geometry().left()
        self.assertLess(initial_baseline_left, editor.solution_strip_slot.geometry().left())
        self.assertEqual(editor.solution_strip.height(), 40)
        self.assertTrue(all(button.height() == 34 for button in editor.solution_tab_buttons))
        self.assertEqual(editor.open_folder_btn.size(), editor.delete_btn.size())
        self.assertEqual(editor.back_btn.height(), editor.open_folder_btn.height())
        self.assertEqual(editor.baseline_menu_button.height(), editor.open_folder_btn.height())
        editor.refresh_gallery(0)
        self.app.processEvents()
        self.assertEqual(editor.solution_controls.geometry(), solution_controls_geometry)
        main_actions = {
            action.text(): action
            for action in editor.baseline_menu_button.menu().actions()
            if action.text()
        }
        self.assertFalse(main_actions["Del"].isEnabled())
        editor.refresh_gallery(1)
        self.app.processEvents()
        self.assertEqual(editor.solution_controls.geometry(), solution_controls_geometry)
        editor.image_gallery.sgf_board.setFocus()
        self.app.processEvents()
        QTest.keyClick(editor.image_gallery.sgf_board, Qt.Key_Left, Qt.ControlModifier)
        self.assertEqual(editor.selected_image_index, 0)
        QTest.keyClick(editor.image_gallery.sgf_board, Qt.Key_Right, Qt.ControlModifier)
        self.assertEqual(editor.selected_image_index, 1)
        editor.description_edit.setFocus()
        editor.description_edit.setPlainText("two words")
        editor.description_edit.moveCursor(QTextCursor.End)
        self.app.processEvents()
        QTest.keyClick(editor.description_edit, Qt.Key_Left, Qt.ControlModifier)
        self.assertEqual(editor.selected_image_index, 1)
        self.assertLess(
            editor.description_edit.textCursor().position(), len("two words")
        )
        self.assertTrue(editor.save_current())
        self.assertIn("TR[ai]", sgf_path.read_text(encoding="utf-8"))
        saved = load_position(self.cfg, position_id)
        self.assertEqual(saved["solution_images"][0]["kind"], "board")
        self.assertEqual(saved["solution_images"][0]["sgf_start_path"], [0])

        # The primary position plus five variations fit before overflow is needed.
        for _ in range(4):
            editor.add_solution_board()
        self.app.processEvents()
        self.assertEqual(
            [button.text() for button in editor.solution_tab_buttons],
            ["Primary", "V1", "V2", "V3", "V4", "V5"],
        )
        self.assertEqual(editor.solution_strip.geometry().left(), initial_strip_left)
        self.assertGreater(editor.solution_strip.width(), initial_strip_width)
        self.assertLessEqual(editor.solution_strip.width(), editor.solution_strip_slot.width())
        self.assertEqual(editor.baseline_menu_button.geometry().left(), initial_baseline_left)

    def test_position_without_sgf_offers_creation_from_both_menus(self):
        position_id = "p000002"
        directory = self.cfg.positions_dir / position_id
        directory.mkdir()
        save_position(self.cfg, position_id, {
            "description": "",
            "score": "",
            "tags": [],
            "metadata": {},
            "solution_images": [],
        })

        editor = PositionEditor(self.cfg)
        self.assertTrue(editor.load_position(position_id))
        baseline_actions = {
            action.text(): action
            for action in editor.baseline_menu_button.menu().actions()
            if action.text()
        }
        self.assertIn("Set SGF", baseline_actions)
        self.assertNotIn("Set current SGF node", baseline_actions)
        self.assertIn(
            "New SGF",
            {action.text() for action in baseline_actions["Set SGF"].menu().actions()},
        )

        baseline_actions["Set SGF"].menu().actions()[0].trigger()
        self.assertIsNotNone(editor.pending_sgf_text)
        self.assertIsNotNone(editor.image_gallery.sgf_board.current_frame)
        self.assertIn(
            "Set current SGF node",
            {
                action.text() for action in editor.baseline_menu_button.menu().actions()
                if action.text()
            },
        )
        self.assertTrue(editor.save_current())
        self.assertTrue((directory / self.cfg.sgf_filename).exists())
        editor.close()

    def test_convert_action_is_conditional_and_creates_setup_sgf(self):
        position_id = "p000003"
        directory = self.cfg.positions_dir / position_id
        directory.mkdir()
        image_path = directory / self.cfg.image_filename
        image = QPixmap(QSize(120, 120))
        image.fill(QColor("#d9b46c"))
        self.assertTrue(image.save(str(image_path)))
        original_bytes = image_path.read_bytes()
        save_position(self.cfg, position_id, {
            "description": "",
            "score": "",
            "main_media_kind": "board",
            "tags": [],
            "metadata": {},
            "solution_images": [],
        })
        editor = PositionEditor(self.cfg)
        self.assertTrue(editor.load_position(position_id))
        actions = {action.text() for action in editor.baseline_menu_button.menu().actions()}
        self.assertNotIn("Convert to SGF…", actions)

        editor._set_selected_media_kind("image")
        actions = {
            action.text(): action for action in editor.baseline_menu_button.menu().actions()
            if action.text()
        }
        self.assertIn("From selected image", {
            action.text() for action in actions["Set SGF"].menu().actions()
        })
        self.assertGreaterEqual(
            sum(
                action.isSeparator()
                for action in editor.baseline_menu_button.menu().actions()
            ),
            3,
        )
        with patch("go_position_db.gui.LizGobanRecognitionDialog") as review_dialog:
            review_dialog.return_value.exec.return_value = QDialog.Accepted
            review_dialog.return_value.result = RecognitionResult(
                19, frozenset({(0, 0), (3, 3)}), frozenset({(18, 18)}),
                player_to_move="W", komi=7.5,
            )
            actions["Set SGF"].menu().actions()[-1].trigger()
        review_dialog.assert_called_once()
        self.assertEqual(editor.main_media_kind, "board")
        self.assertEqual(editor.main_sgf_start_path, [])
        self.assertIn("AB[as][dp]", editor.pending_sgf_text)
        self.assertIn("AW[sa]", editor.pending_sgf_text)
        self.assertIn("PL[W]", editor.pending_sgf_text)
        self.assertIn("KM[7.5]", editor.pending_sgf_text)
        self.assertEqual(image_path.read_bytes(), original_bytes)
        self.assertTrue(editor.save_current())
        self.assertEqual(image_path.read_bytes(), original_bytes)
        self.assertTrue((directory / self.cfg.sgf_filename).exists())
        editor.close()

    def test_set_baseline_creates_from_clipboard_without_intermediate_steps(self):
        position_id = "p000003-direct"
        directory = self.cfg.positions_dir / position_id
        directory.mkdir()
        save_position(self.cfg, position_id, {
            "description": "", "score": "", "tags": [], "metadata": {},
            "solution_images": [],
        })
        clipboard_image = QPixmap(QSize(90, 90))
        clipboard_image.fill(QColor("#d9b46c"))
        QApplication.clipboard().setPixmap(clipboard_image)
        editor = PositionEditor(self.cfg)
        try:
            self.assertTrue(editor.load_position(position_id))
            create_from_image = {
                action.text(): action for action in editor.baseline_menu_button.menu().actions()
                if action.text()
            }["Set SGF"]
            source_actions = {
                action.text(): action for action in create_from_image.menu().actions()
                if action.text()
            }
            self.assertEqual(
                set(source_actions),
                {"New SGF", "From files…", "From clipboard", "From selected image"},
            )
            self.assertTrue(source_actions["From clipboard"].isEnabled())
            self.assertFalse(source_actions["From selected image"].isEnabled())
            with patch("go_position_db.gui.LizGobanRecognitionDialog") as review_dialog:
                review_dialog.return_value.exec.return_value = QDialog.Accepted
                review_dialog.return_value.result = RecognitionResult(
                    19, frozenset({(4, 4)}), frozenset({(5, 5)})
                )
                source_actions["From clipboard"].trigger()
            review_dialog.assert_called_once()
            self.assertIsNotNone(editor.pending_image)
            self.assertEqual(editor.main_media_kind, "board")
            self.assertIn("AB[eo]", editor.pending_sgf_text)
            self.assertIn("AW[fn]", editor.pending_sgf_text)
            self.assertTrue(editor.save_current())
            self.assertTrue((directory / self.cfg.image_filename).exists())
            self.assertTrue((directory / self.cfg.sgf_filename).exists())
        finally:
            editor.close()
            QApplication.clipboard().clear()

    def test_solution_conversion_adds_branch_and_reassigns_only_selected_baseline(self):
        class Recognizer:
            def recognize(self, _path, *, board_size):
                return RecognitionResult(
                    board_size, frozenset({(2, 2)}), frozenset({(3, 3)})
                )

        position_id = "p000004"
        directory = self.cfg.positions_dir / position_id
        solution_dir = directory / "solutions"
        solution_dir.mkdir(parents=True)
        sgf_path = directory / self.cfg.sgf_filename
        original_sgf = "(;GM[1]FF[4]SZ[19]C[root];B[aa](;W[bb]C[existing]))"
        sgf_path.write_text(original_sgf, encoding="utf-8")
        solution_path = solution_dir / "solution-001.png"
        image = QPixmap(QSize(100, 100))
        image.fill(QColor("#d9b46c"))
        self.assertTrue(image.save(str(solution_path)))
        original_image = solution_path.read_bytes()
        save_position(self.cfg, position_id, {
            "description": "",
            "score": "",
            "main_media_kind": "board",
            "sgf_start_path": [],
            "tags": [],
            "metadata": {},
            "solution_images": [{
                "kind": "image", "file": "solutions/solution-001.png",
                "description": "", "score": "", "sgf_start_path": [0],
            }],
        })
        editor = PositionEditor(self.cfg, recognition_service=Recognizer())
        self.assertTrue(editor.load_position(position_id))
        editor.refresh_gallery(1)
        with patch("go_position_db.gui.QMessageBox.question", return_value=QMessageBox.Yes):
            editor.convert_selected_image_to_sgf()
        self.assertEqual(editor.solution_images[0]["kind"], "board")
        self.assertEqual(editor.solution_images[0]["sgf_start_path"], [0, 1])
        self.assertEqual(editor.main_sgf_start_path, [])
        self.assertIn("C[existing]", editor.pending_sgf_text)
        self.assertEqual(solution_path.read_bytes(), original_image)
        self.assertTrue(editor.save_current())
        self.assertIn("C[existing]", sgf_path.read_text(encoding="utf-8"))
        self.assertEqual(solution_path.read_bytes(), original_image)
        editor.close()

    def test_recognition_failure_and_malformed_result_do_not_change_editor(self):
        class Failure:
            def recognize(self, _path, *, board_size):
                raise RecognitionError("grid not found")

        class Malformed:
            def recognize(self, _path, *, board_size):
                return {"black": [[0, 0]]}

        position_id = "p000005"
        directory = self.cfg.positions_dir / position_id
        directory.mkdir()
        image_path = directory / self.cfg.image_filename
        image = QPixmap(QSize(80, 80))
        image.fill(QColor("#d9b46c"))
        self.assertTrue(image.save(str(image_path)))
        original_image = image_path.read_bytes()
        save_position(self.cfg, position_id, {
            "description": "", "score": "", "main_media_kind": "image",
            "tags": [], "metadata": {}, "solution_images": [],
        })
        for service in (Failure(), Malformed()):
            editor = PositionEditor(self.cfg, recognition_service=service)
            self.assertTrue(editor.load_position(position_id))
            with patch("go_position_db.gui.QMessageBox.critical") as critical:
                editor.convert_selected_image_to_sgf()
            critical.assert_called_once()
            self.assertIsNone(editor.pending_sgf_text)
            self.assertEqual(editor.main_media_kind, "image")
            self.assertEqual(image_path.read_bytes(), original_image)
            editor.close()

    def test_missing_recognizer_is_explained_without_changing_editor(self):
        class Unavailable:
            def recognize(self, _path, *, board_size):
                raise RecognitionUnavailableError("Recognition support is missing.")

        position_id = "p000006"
        directory = self.cfg.positions_dir / position_id
        directory.mkdir()
        image_path = directory / self.cfg.image_filename
        image = QPixmap(QSize(80, 80))
        image.fill(QColor("#d9b46c"))
        self.assertTrue(image.save(str(image_path)))
        save_position(self.cfg, position_id, {
            "description": "", "score": "", "main_media_kind": "image",
            "tags": [], "metadata": {}, "solution_images": [],
        })
        editor = PositionEditor(self.cfg, recognition_service=Unavailable())
        self.assertTrue(editor.load_position(position_id))
        with patch("go_position_db.gui.QMessageBox.information") as information:
            editor.convert_selected_image_to_sgf()
        information.assert_called_once()
        self.assertIsNone(editor.pending_sgf_text)
        self.assertEqual(editor.main_media_kind, "image")
        self.assertTrue(image_path.exists())
        editor.close()


if __name__ == "__main__":
    unittest.main()
