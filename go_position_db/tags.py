from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import re
from typing import Any, Iterable

from .config import Config
from .storage import DatabaseError, atomic_dump_yaml, load_yaml


BOOLEAN_TAG_OPERATORS = {"and", "or", "not"}


def normalize_tag_name(name: str) -> str:
    normalized = re.sub(r"[\s_]+", "-", name.strip().casefold())
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized


def validate_new_tag_name(name: str) -> str:
    normalized = normalize_tag_name(name)
    if not normalized:
        raise DatabaseError("Tag name cannot be empty.")
    if normalized in BOOLEAN_TAG_OPERATORS:
        raise DatabaseError(f"'{normalized}' is reserved as a Boolean search operator and cannot be a tag.")
    return normalized


@dataclass
class TagInfo:
    name: str
    parents: list[str]
    description: str = ""
    metadata: dict[str, Any] | None = None


class TagGraph:
    def __init__(self, config: Config, raw: dict[str, Any] | None = None):
        self.config = config
        self.raw = raw if raw is not None else (load_yaml(config.tags_file, {}) or {})
        if "tags" in self.raw and isinstance(self.raw["tags"], dict):
            self.entries = self.raw["tags"]
        else:
            # Accept the compact historical format where the file itself is the mapping.
            self.entries = self.raw
        if not isinstance(self.entries, dict):
            raise DatabaseError("tags.yaml must contain a mapping of tag names.")
        normalized_entries: dict[str, Any] = {}
        changed = False
        for name, raw_entry in self.entries.items():
            if not isinstance(name, str):
                raise DatabaseError("Every tag name must be a string.")
            canonical_name = validate_new_tag_name(name)
            if canonical_name in normalized_entries:
                raise DatabaseError(f"Conflicting tags normalize to '{canonical_name}'.")
            entry = dict(raw_entry or {}) if isinstance(raw_entry, dict) else raw_entry
            if isinstance(entry, dict):
                parents = entry.get("parents", []) or []
                if isinstance(parents, list) and all(isinstance(parent, str) for parent in parents):
                    normalized_parents = [validate_new_tag_name(parent) for parent in parents]
                    if normalized_parents != parents:
                        entry["parents"] = normalized_parents
                        changed = True
            normalized_entries[canonical_name] = entry
            changed = changed or canonical_name != name
        self.entries = normalized_entries
        self._canonical = {name: name for name in self.entries}
        if raw is None and changed:
            self._save_entries()

    def normalize(self, name: str) -> str:
        return normalize_tag_name(name)

    def canonical(self, name: str) -> str:
        key = self.normalize(name)
        if key not in self._canonical:
            raise DatabaseError(f"Unknown tag '{name}'.")
        return self._canonical[key]

    def has(self, name: str) -> bool:
        return self.normalize(name) in self._canonical

    def info(self, name: str) -> TagInfo:
        canonical = self.canonical(name)
        raw = self.entries.get(canonical) or {}
        if not isinstance(raw, dict):
            raise DatabaseError(f"Definition for tag '{canonical}' must be a mapping.")
        parents = raw.get("parents", []) or []
        if not isinstance(parents, list) or not all(isinstance(p, str) for p in parents):
            raise DatabaseError(f"Parents for tag '{canonical}' must be a list of strings.")
        return TagInfo(
            name=canonical,
            parents=parents,
            description=str(raw.get("description", "") or ""),
            metadata=raw.get("metadata", {}) or {},
        )

    def names(self) -> list[str]:
        return sorted(self.entries, key=str.casefold)

    def parents(self, name: str) -> list[str]:
        return [self.canonical(p) for p in self.info(name).parents]

    def children_map(self) -> dict[str, list[str]]:
        children: dict[str, list[str]] = defaultdict(list)
        for tag in self.names():
            for parent in self.parents(tag):
                children[parent].append(tag)
        for values in children.values():
            values.sort(key=str.casefold)
        return dict(children)

    def ancestors(self, name: str, include_self: bool = True) -> set[str]:
        start = self.canonical(name)
        out: set[str] = {start} if include_self else set()
        stack = list(self.parents(start))
        while stack:
            tag = stack.pop()
            if tag in out:
                continue
            out.add(tag)
            stack.extend(self.parents(tag))
        return out

    def descendants(self, name: str, include_self: bool = True) -> set[str]:
        start = self.canonical(name)
        children = self.children_map()
        out: set[str] = {start} if include_self else set()
        stack = list(children.get(start, []))
        while stack:
            tag = stack.pop()
            if tag in out:
                continue
            out.add(tag)
            stack.extend(children.get(tag, []))
        return out

    def expanded_tags(self, explicit_tags: Iterable[str]) -> set[str]:
        out: set[str] = set()
        for tag in explicit_tags:
            out.update(self.ancestors(tag, include_self=True))
        return out

    def validate(self) -> list[str]:
        errors: list[str] = []
        for tag in self.names():
            raw = self.entries.get(tag) or {}
            if not isinstance(raw, dict):
                errors.append(f"Tag '{tag}' definition is not a mapping.")
                continue
            parents = raw.get("parents", []) or []
            if not isinstance(parents, list) or not all(isinstance(p, str) for p in parents):
                errors.append(f"Tag '{tag}' has invalid parents; expected a list of strings.")
                continue
            for parent in parents:
                if not self.has(parent):
                    errors.append(f"Tag '{tag}' has unknown parent '{parent}'.")

        # Cycle detection only when parent references are valid enough to traverse.
        state: dict[str, int] = {tag: 0 for tag in self.names()}  # 0 unseen, 1 active, 2 done
        path: list[str] = []

        def visit(tag: str) -> None:
            if state[tag] == 2:
                return
            if state[tag] == 1:
                try:
                    i = path.index(tag)
                    cycle = path[i:] + [tag]
                except ValueError:
                    cycle = [tag, tag]
                errors.append("Tag inheritance cycle: " + " -> ".join(cycle))
                return
            state[tag] = 1
            path.append(tag)
            try:
                parents = self.info(tag).parents
            except DatabaseError:
                parents = []
            for parent in parents:
                if self.has(parent):
                    visit(self.canonical(parent))
            path.pop()
            state[tag] = 2

        for tag in self.names():
            if state[tag] == 0:
                visit(tag)
        return list(dict.fromkeys(errors))

    def _save_entries(self) -> None:
        atomic_dump_yaml(self.config.tags_file, {"tags": self.entries})
        self.raw = {"tags": self.entries}
        self._canonical = {self.normalize(name): name for name in self.entries}

    def add(self, name: str, parents: Iterable[str] = (), description: str = "") -> None:
        name = validate_new_tag_name(name)
        if self.has(name):
            raise DatabaseError(f"Tag '{name}' already exists.")
        canonical_parents = [self.canonical(p) for p in parents]
        self.entries[name] = {"parents": list(dict.fromkeys(canonical_parents))}
        if description:
            self.entries[name]["description"] = description
        self._canonical[self.normalize(name)] = name
        errors = self.validate()
        if errors:
            del self.entries[name]
            self._canonical.pop(self.normalize(name), None)
            raise DatabaseError("Cannot add tag:\n" + "\n".join(errors))
        self._save_entries()

    def remove(self, name: str, force: bool = False) -> None:
        canonical = self.canonical(name)
        children = [c for c, info in ((x, self.info(x)) for x in self.names()) if canonical in [self.canonical(p) for p in info.parents]]
        if children and not force:
            raise DatabaseError(f"Tag '{canonical}' is a parent of: {', '.join(children)}. Use --force to remove those parent links.")
        if force:
            for child in children:
                raw = self.entries[child] or {}
                raw["parents"] = [p for p in raw.get("parents", []) if self.normalize(p) != self.normalize(canonical)]
                self.entries[child] = raw
        del self.entries[canonical]
        self._save_entries()

    def add_parent(self, name: str, parent: str) -> None:
        tag = self.canonical(name)
        parent = self.canonical(parent)
        raw = self.entries[tag] or {}
        parents = list(raw.get("parents", []) or [])
        if parent not in [self.canonical(p) for p in parents]:
            parents.append(parent)
        old = list(raw.get("parents", []) or [])
        raw["parents"] = parents
        self.entries[tag] = raw
        errors = self.validate()
        if errors:
            raw["parents"] = old
            raise DatabaseError("Cannot add parent:\n" + "\n".join(errors))
        self._save_entries()

    def remove_parent(self, name: str, parent: str) -> None:
        tag = self.canonical(name)
        parent_canonical = self.canonical(parent)
        raw = self.entries[tag] or {}
        parents = list(raw.get("parents", []) or [])
        new_parents = [p for p in parents if self.normalize(p) != self.normalize(parent_canonical)]
        if len(new_parents) == len(parents):
            raise DatabaseError(f"'{parent_canonical}' is not a parent of '{tag}'.")
        raw["parents"] = new_parents
        self.entries[tag] = raw
        self._save_entries()

    def add_child(self, name: str, child: str) -> None:
        self.add_parent(child, name)

    def remove_child(self, name: str, child: str) -> None:
        self.remove_parent(child, name)

    def tree_lines(self) -> list[str]:
        children = self.children_map()
        roots = [tag for tag in self.names() if not self.parents(tag)]
        lines: list[str] = []
        shown_paths: set[tuple[str, ...]] = set()

        def walk(tag: str, prefix: str, path: tuple[str, ...]) -> None:
            lines.append(prefix + tag)
            for child in children.get(tag, []):
                edge_path = path + (child,)
                if edge_path in shown_paths:
                    continue
                shown_paths.add(edge_path)
                walk(child, prefix + "  ", edge_path)

        for root in roots:
            walk(root, "", (root,))
        # Invalid/orphan/cyclic tags still get shown at least once.
        displayed = {line.strip() for line in lines}
        for tag in self.names():
            if tag not in displayed:
                lines.append(tag)
        return lines
