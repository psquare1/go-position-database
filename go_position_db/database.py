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
    save_position,
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

    def cleanup_position_tags(self) -> dict[str, tuple[list[str], list[str]]]:
        """Remove unknown/redundant tags, canonicalize the rest, and report changes."""
        graph = self.tag_graph()
        changes: dict[str, tuple[list[str], list[str]]] = {}
        for position_id, record in self.all_positions().items():
            before = list(record.get("tags", []))
            # Manual edits, imports, or an interrupted historical tag deletion
            # can leave references whose definitions no longer exist. Discard
            # those before canonicalizing and removing implied ancestors.
            known = [tag for tag in before if graph.has(tag)]
            after = graph.minimal_explicit_tags(known)
            if after != before:
                record["tags"] = after
                save_position(self.config, position_id, record)
                changes[position_id] = (before, after)
        return changes

    def delete_tag(self, name: str, *, force: bool = False) -> list[str]:
        """Delete a tag and its direct position references as one recoverable operation."""
        graph = self.tag_graph()
        canonical = graph.canonical(name)
        positions = self.all_positions()
        affected: dict[str, dict[str, Any]] = {}
        updated: dict[str, dict[str, Any]] = {}
        for position_id, record in positions.items():
            retained = [
                tag for tag in record.get("tags", [])
                if graph.normalize(tag) != graph.normalize(canonical)
            ]
            if retained != record.get("tags", []):
                affected[position_id] = record
                replacement = dict(record)
                replacement["tags"] = retained
                updated[position_id] = replacement

        # Preserve both sides so a later metadata or index write cannot leave a
        # deleted definition paired with stale position references (or vice versa).
        tags_backup = {"tags": {key: dict(value or {}) for key, value in graph.entries.items()}}
        try:
            graph.remove(canonical, force=force)
            for position_id, record in updated.items():
                save_position(self.config, position_id, record)
            self.rebuild_index()
        except Exception:
            atomic_dump_yaml(self.config.tags_file, tags_backup)
            for position_id, record in affected.items():
                save_position(self.config, position_id, record)
            raise
        return sorted(affected)

    def rebuild_index(self) -> dict[str, Any]:
        # A newly added hierarchy edge can make a formerly explicit ancestor
        # redundant. Rebuilds are the common path after hierarchy edits, CLI
        # writes, imports, and startup maintenance, so normalize before indexing.
        self.cleanup_position_tags()
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
            media_resolution_failed = False
            try:
                image = position_image_path(self.config, position_id)
            except DatabaseError as e:
                image = None
                media_resolution_failed = True
                if not any(str(e) in existing for existing in errors):
                    errors.append(f"{position_id}: {e}")
            try:
                sgf = position_sgf_path(self.config, position_id)
            except DatabaseError as e:
                sgf = None
                media_resolution_failed = True
                if not any(str(e) in existing for existing in errors):
                    errors.append(f"{position_id}: {e}")
            if not media_resolution_failed and image is None and sgf is None:
                errors.append(f"{position_id}: missing main image or SGF")
            if not meta.exists():
                errors.append(f"{position_id}: missing {self.config.metadata_filename}")
                continue
            try:
                record = load_position(self.config, position_id)
            except DatabaseError as e:
                errors.append(f"{position_id}: {e}")
                continue
            for solution_index, solution in enumerate(record.get("solution_images", []), start=1):
                if solution.get("kind") == "board":
                    continue
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
