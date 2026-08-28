from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .query import QueryParser
from .storage import (
    DatabaseError,
    atomic_dump_yaml,
    entry_file_issues,
    iter_position_ids,
    load_position,
    load_yaml,
    position_dir,
    position_image_path,
    position_sgf_path,
)
from .tags import TagGraph


class GoPositionDatabase:
    def __init__(self, config: Config):
        self.config = config

    def tag_graph(self) -> TagGraph:
        return TagGraph(self.config)

    def all_positions(self) -> dict[str, dict[str, Any]]:
        return {position_id: load_position(self.config, position_id) for position_id in iter_position_ids(self.config)}

    def build_index_data(self) -> dict[str, Any]:
        graph = self.tag_graph()
        graph_errors = graph.validate()
        if graph_errors:
            raise DatabaseError("Cannot build index while tag graph is invalid:\n" + "\n".join(graph_errors))

        positions = self.all_positions()
        explicit: dict[str, list[str]] = {tag: [] for tag in graph.names()}
        inherited: dict[str, list[str]] = {tag: [] for tag in graph.names()}
        expanded_by_position: dict[str, list[str]] = {}

        for position_id, record in positions.items():
            canonical_explicit: list[str] = []
            for tag in record["tags"]:
                canonical = graph.canonical(tag)
                canonical_explicit.append(canonical)
                explicit.setdefault(canonical, []).append(position_id)
            expanded = sorted(graph.expanded_tags(canonical_explicit), key=str.casefold)
            expanded_by_position[position_id] = expanded
            for tag in expanded:
                inherited.setdefault(tag, []).append(position_id)

        for mapping in (explicit, inherited):
            for tag, ids in mapping.items():
                mapping[tag] = sorted(set(ids))

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tag_to_positions": inherited,
            "explicit_tag_to_positions": explicit,
            "position_to_expanded_tags": expanded_by_position,
        }

    def rebuild_index(self) -> dict[str, Any]:
        data = self.build_index_data()
        atomic_dump_yaml(self.config.index_file, data)
        return data

    def search(self, query: str) -> list[str]:
        graph = self.tag_graph()
        errors = graph.validate()
        if errors:
            raise DatabaseError("Tag graph is invalid:\n" + "\n".join(errors))

        positions = self.all_positions()
        universe = set(positions)
        by_expanded_tag: dict[str, set[str]] = {tag: set() for tag in graph.names()}
        for position_id, record in positions.items():
            for explicit_tag in record["tags"]:
                for expanded_tag in graph.ancestors(explicit_tag):
                    by_expanded_tag.setdefault(expanded_tag, set()).add(position_id)

        def lookup(tag: str) -> set[str]:
            canonical = graph.canonical(tag)
            return by_expanded_tag.get(canonical, set())

        return sorted(QueryParser(query, lookup, universe).parse())

    def check(self) -> list[str]:
        errors: list[str] = []
        graph = self.tag_graph()
        errors.extend(graph.validate())

        for position_id in iter_position_ids(self.config):
            d = position_dir(self.config, position_id)
            meta = d / self.config.metadata_filename
            errors.extend(entry_file_issues(self.config, position_id))
            try:
                image = position_image_path(self.config, position_id)
            except DatabaseError as e:
                image = None
                if not any(str(e) in existing for existing in errors):
                    errors.append(f"{position_id}: {e}")
            if image is None:
                errors.append(f"{position_id}: missing image ({', '.join(self.config.image_extensions)})")
            # SGF is optional. If present, resolution/ambiguity is still validated.
            try:
                position_sgf_path(self.config, position_id)
            except DatabaseError as e:
                if not any(str(e) in existing for existing in errors):
                    errors.append(f"{position_id}: {e}")
            if not meta.exists():
                errors.append(f"{position_id}: missing {self.config.metadata_filename}")
                continue
            try:
                record = load_position(self.config, position_id)
            except DatabaseError as e:
                errors.append(f"{position_id}: {e}")
                continue
            for solution_index, solution in enumerate(record.get("solution_images", []), start=1):
                solution_path = d / solution["file"]
                if not solution_path.exists():
                    errors.append(f"{position_id}: missing solution image {solution_index} ({solution['file']})")
                elif solution_path.suffix.lower() not in self.config.image_extensions:
                    errors.append(f"{position_id}: unsupported solution image extension ({solution['file']})")
            seen: set[str] = set()
            for tag in record["tags"]:
                key = graph.normalize(tag)
                if key in seen:
                    errors.append(f"{position_id}: duplicate tag '{tag}'")
                seen.add(key)
                if not graph.has(tag):
                    errors.append(f"{position_id}: unknown tag '{tag}'")

        if self.config.index_file.exists() and not errors:
            saved = load_yaml(self.config.index_file, {}) or {}
            expected = self.build_index_data()
            # Ignore timestamp when checking compatibility.
            saved_cmp = dict(saved)
            expected_cmp = dict(expected)
            saved_cmp.pop("generated_at", None)
            expected_cmp.pop("generated_at", None)
            if saved_cmp != expected_cmp:
                errors.append("Generated tag index is stale or inconsistent; run 'rebuild-index'.")
        return errors
