from pathlib import Path
import tempfile
import unittest

import yaml

from go_position_db.config import Config
from go_position_db.database import GoPositionDatabase
from go_position_db.query import QueryParser
from go_position_db.storage import (
    atomic_dump_yaml,
    clean_position_files,
    formatted_score,
    load_position,
    position_image_path,
    position_sgf_path,
)
from go_position_db.tags import TagGraph


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = Config.defaults(root)
        self.cfg.positions_dir.mkdir(parents=True)
        self.cfg.index_file.parent.mkdir(parents=True)
        atomic_dump_yaml(self.cfg.tags_file, {
            "tags": {
                "joseki": {"parents": []},
                "corner-joseki": {"parents": ["joseki"]},
                "3-3-joseki": {"parents": ["corner-joseki"]},
                "sente": {"parents": []},
                "reverse-sente": {"parents": ["sente"]},
                "large reverse-sente": {"parents": ["reverse-sente"]},
            }
        })
        for pid, tags in {
            "p1": ["3-3-joseki", "reverse-sente"],
            "p2": ["joseki"],
            "p3": ["large reverse-sente"],
        }.items():
            d = self.cfg.positions_dir / pid
            d.mkdir()
            (d / self.cfg.sgf_filename).write_text("(;GM[1])", encoding="utf-8")
            (d / self.cfg.image_filename).write_bytes(b"png")
            atomic_dump_yaml(d / self.cfg.metadata_filename, {"description": pid, "tags": tags, "metadata": {}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_inheritance_search(self):
        db = GoPositionDatabase(self.cfg)
        self.assertEqual(db.search("joseki"), ["p1", "p2"])
        self.assertEqual(db.search("sente"), ["p1", "p3"])

    def test_boolean_search(self):
        db = GoPositionDatabase(self.cfg)
        q = '(joseki AND reverse-sente) OR "large reverse-sente"'
        self.assertEqual(db.search(q), ["p1", "p3"])
        self.assertEqual(db.search("joseki AND NOT reverse-sente"), ["p2"])


    def test_autodetect_and_clean_files(self):
        d = self.cfg.positions_dir / "p1"
        (d / self.cfg.sgf_filename).rename(d / "random-name.SGF")
        (d / self.cfg.image_filename).rename(d / "board-shot.JPG")

        self.assertEqual(position_sgf_path(self.cfg, "p1").name, "random-name.SGF")
        self.assertEqual(position_image_path(self.cfg, "p1").name, "board-shot.JPG")

        actions = clean_position_files(self.cfg, "p1")
        self.assertIn("random-name.SGF -> position.sgf", actions)
        self.assertIn("board-shot.JPG -> position.jpg", actions)
        self.assertEqual(position_sgf_path(self.cfg, "p1").name, "position.sgf")
        self.assertEqual(position_image_path(self.cfg, "p1").name, "position.jpg")

    def test_sgf_optional_but_image_required(self):
        d = self.cfg.positions_dir / "p2"
        (d / self.cfg.sgf_filename).unlink()
        db = GoPositionDatabase(self.cfg)
        self.assertFalse(any("p2: missing" in error and "SGF" in error for error in db.check()))

        (d / self.cfg.image_filename).unlink()
        errors = db.check()
        self.assertTrue(any("p2: missing image" in error for error in errors))

    def test_cycle_detection(self):
        from go_position_db.storage import DatabaseError
        g = TagGraph(self.cfg)
        with self.assertRaises(DatabaseError):
            g.add_parent("joseki", "3-3-joseki")

    def test_position_score_and_solution_images(self):
        record = load_position(self.cfg, "p1")
        record.update({
            "name": "Retired display name",
            "score": "B +3.5",
            "sgf_start_path": [0, 1],
            "solution_images": [
                {
                    "file": "solutions/solution-001.png",
                    "description": "White resists",
                    "score": "W +1.5",
                    "sgf_start_path": [0, 1, 2],
                },
                {
                    "kind": "board",
                    "description": "An SGF-only continuation",
                    "score": "",
                    "sgf_start_path": [0, 2],
                },
            ],
        })
        from go_position_db.storage import save_position
        save_position(self.cfg, "p1", record)
        loaded = load_position(self.cfg, "p1")
        self.assertNotIn("name", loaded)
        self.assertEqual(loaded["sgf_start_path"], [0, 1])
        self.assertEqual(loaded["solution_images"][0]["score"], "W +1.5")
        self.assertEqual(loaded["solution_images"][0]["sgf_start_path"], [0, 1, 2])
        self.assertEqual(loaded["solution_images"][1]["kind"], "board")
        self.assertEqual(loaded["solution_images"][1]["file"], "")
        self.assertEqual(formatted_score(loaded["score"]), "B +3.5")
        self.assertEqual(formatted_score("3.5"), "B +3.5")
        self.assertEqual(formatted_score("+2"), "B +2")
        self.assertEqual(formatted_score("-6.3"), "W +6.3")
        self.assertEqual(formatted_score(-1.25), "W +1.25")
        self.assertIsNone(formatted_score("black wins"))
        record["score"] = -2.5
        record["solution_images"][0]["score"] = 3
        save_position(self.cfg, "p1", record)
        normalized = load_position(self.cfg, "p1")
        self.assertEqual(normalized["score"], "W +2.5")
        self.assertEqual(normalized["solution_images"][0]["score"], "B +3")

        from go_position_db.storage import DatabaseError
        record["sgf_start_path"] = [0, -1]
        with self.assertRaises(DatabaseError):
            save_position(self.cfg, "p1", record)

    def test_tag_names_are_canonical_and_operators_are_reserved(self):
        graph = TagGraph(self.cfg)
        self.assertTrue(graph.has("LARGE_reverse sente"))
        self.assertEqual(graph.canonical("LARGE_reverse sente"), "large-reverse-sente")
        graph.add("New_Tag Name")
        self.assertTrue(graph.has("new-tag-name"))
        self.assertIn("new-tag-name", graph.names())
        from go_position_db.storage import DatabaseError
        for operator in ("and", "OR", "Not"):
            with self.assertRaises(DatabaseError):
                graph.add(operator)
        graph.add_child("joseki", "new-tag-name")
        self.assertIn("new-tag-name", graph.children_map()["joseki"])
        self.assertIn("joseki", graph.parents("new-tag-name"))
        graph.remove_child("joseki", "new-tag-name")
        self.assertNotIn("joseki", graph.parents("new-tag-name"))
        record = load_position(self.cfg, "p2")
        record["tags"] = ["New_Tag Name"]
        from go_position_db.storage import save_position
        save_position(self.cfg, "p2", record)
        self.assertEqual(load_position(self.cfg, "p2")["tags"], ["new-tag-name"])


if __name__ == "__main__":
    unittest.main()
