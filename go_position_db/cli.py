from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

import yaml

from .config import DEFAULT_ROOT, default_config_yaml, load_config
from .database import GoPositionDatabase
from .storage import (
    DatabaseError,
    clean_position_files,
    create_position,
    delete_nested,
    get_nested,
    load_position,
    parse_cli_value,
    position_dir,
    position_image_path,
    position_sgf_path,
    save_position,
    set_nested,
)
from .tags import TagGraph


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="go_db.py",
        description="Tag, validate, and search a filesystem database of annotated Go positions.",
    )
    p.add_argument("--root", type=Path, default=None, help=f"Database root (default: GO_POSITION_DB_ROOT or {DEFAULT_ROOT})")
    p.add_argument("--config", type=Path, default=None, help="Optional path to config.yaml")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create the database folders and starter config/tag files.")
    init.add_argument("--force", action="store_true", help="Overwrite starter config/tags files if present.")

    sub.add_parser("gui", help="Launch the local PySide6 desktop UI.")

    search = sub.add_parser("search", help="Search using Boolean tag expressions.")
    search.add_argument("query", help="Example: '(joseki AND reverse-sente) OR large-reverse-sente'")
    search.add_argument("--verbose", action="store_true", help="Show description, explicit tags, and paths.")
    search.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    search.add_argument("--limit", type=int, default=None, help="Maximum results to print.")

    sub.add_parser("check", help="Validate positions, tags, inheritance, and generated index consistency.")
    sub.add_parser("rebuild-index", help="Regenerate the redundant tag index from canonical position metadata.")
    clean = sub.add_parser("clean", help="Normalize arbitrary SGF/image filenames inside position folders.")
    clean.add_argument("position_ids", nargs="*", help="Positions to clean; omit to clean every position.")
    clean.add_argument("--dry-run", action="store_true", help="Show renames without changing files.")

    pos = sub.add_parser("position", aliases=["pos"], help="Create, inspect, or edit a position.")
    pos_sub = pos.add_subparsers(dest="position_command", required=True)

    c = pos_sub.add_parser("create")
    c.add_argument("position_id")
    c.add_argument("--sgf", type=Path, help="Optional SGF file.")
    c.add_argument("--image", type=Path)
    c.add_argument("--description", default="")
    c.add_argument("--tag", action="append", default=[])
    c.add_argument("--meta", action="append", default=[], metavar="KEY=VALUE", help="Repeatable; VALUE is parsed as JSON when possible.")

    s = pos_sub.add_parser("show")
    s.add_argument("position_id")
    s.add_argument("--json", action="store_true")

    a = pos_sub.add_parser("add-tag")
    a.add_argument("position_id")
    a.add_argument("tags", nargs="+")

    r = pos_sub.add_parser("remove-tag")
    r.add_argument("position_id")
    r.add_argument("tags", nargs="+")

    st = pos_sub.add_parser("set-tags", help="Replace the position's explicit tag list.")
    st.add_argument("position_id")
    st.add_argument("tags", nargs="*")

    sd = pos_sub.add_parser("set-description")
    sd.add_argument("position_id")
    sd.add_argument("description")

    ms = pos_sub.add_parser("meta-set")
    ms.add_argument("position_id")
    ms.add_argument("key", help="Dotted keys allowed, e.g. source.book")
    ms.add_argument("value", help="Parsed as JSON when possible; otherwise stored as text.")

    md = pos_sub.add_parser("meta-delete")
    md.add_argument("position_id")
    md.add_argument("key")

    mg = pos_sub.add_parser("meta-show")
    mg.add_argument("position_id")
    mg.add_argument("key", nargs="?")

    tag = sub.add_parser("tag", aliases=["tags"], help="Manage tag definitions and inheritance.")
    tag_sub = tag.add_subparsers(dest="tag_command", required=True)

    tl = tag_sub.add_parser("list")
    tl.add_argument("--tree", action="store_true")

    ta = tag_sub.add_parser("add")
    ta.add_argument("name")
    ta.add_argument("--parent", action="append", default=[])
    ta.add_argument("--description", default="")

    tr = tag_sub.add_parser("remove")
    tr.add_argument("name")
    tr.add_argument("--force", action="store_true", help="Also remove this tag from child parent lists. Position references are still validated separately.")

    tap = tag_sub.add_parser("add-parent")
    tap.add_argument("name")
    tap.add_argument("parent")

    trp = tag_sub.add_parser("remove-parent")
    trp.add_argument("name")
    trp.add_argument("parent")

    return p


def _meta_pairs(items: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise DatabaseError(f"Expected KEY=VALUE for --meta, got {item!r}")
        key, value = item.split("=", 1)
        set_nested(out, key, parse_cli_value(value))
    return out


def _canonicalize_tags(graph: TagGraph, tags: list[str]) -> list[str]:
    return list(dict.fromkeys(graph.canonical(tag) for tag in tags))


def _ensure_root_files(root: Path, force: bool) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "positions").mkdir(exist_ok=True)
    (root / "generated").mkdir(exist_ok=True)
    cfg = root / "config.yaml"
    tags = root / "tags.yaml"
    if force or not cfg.exists():
        cfg.write_text(default_config_yaml(), encoding="utf-8")
    if force or not tags.exists():
        tags.write_text("tags: {}\n", encoding="utf-8")




def _entry_paths(config, position_id: str) -> tuple[Path | None, Path | None]:
    return position_sgf_path(config, position_id), position_image_path(config, position_id)


def _print_search(db: GoPositionDatabase, ids: list[str], verbose: bool, json_mode: bool, limit: int | None) -> None:
    shown = ids if limit is None else ids[: max(limit, 0)]
    if json_mode:
        payload = []
        for pid in shown:
            rec = load_position(db.config, pid)
            sgf, image = _entry_paths(db.config, pid)
            payload.append({
                "id": pid,
                "description": rec["description"],
                "score": rec["score"],
                "tags": rec["tags"],
                "metadata": rec["metadata"],
                "solution_images": rec["solution_images"],
                "sgf": str(sgf) if sgf else None,
                "image": str(image) if image else None,
            })
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    for pid in shown:
        sgf, image = _entry_paths(db.config, pid)
        if not verbose:
            # Prefer the SGF path when present; otherwise print the image path.
            primary = sgf or image or position_dir(db.config, pid)
            print(f"{pid}\t{primary}")
            continue
        rec = load_position(db.config, pid)
        print(pid)
        print(f"  description: {rec['description']}")
        print(f"  score: {rec['score'] or '(none)'}")
        print(f"  tags: {', '.join(rec['tags']) or '(none)'}")
        print(f"  solution images: {len(rec['solution_images'])}")
        print(f"  sgf: {sgf or '(none)'}")
        print(f"  image: {image or '(none)'}")
    suffix = "" if len(shown) == len(ids) else f"; showing {len(shown)}"
    print(f"\n{len(ids)} position(s) found{suffix}.")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = Path(args.root) if args.root else DEFAULT_ROOT

    if args.command == "init":
        _ensure_root_files(root, args.force)
        print(f"Initialized database at {root}")
        return 0

    if args.command == "gui":
        from .gui import run_gui
        return run_gui(root=root, config_path=args.config)

    config = load_config(root=root, config_path=args.config)
    db = GoPositionDatabase(config)

    if args.command == "search":
        ids = db.search(args.query)
        _print_search(db, ids, args.verbose, args.json, args.limit)
        return 0

    if args.command == "check":
        errors = db.check()
        if errors:
            print("Validation failed:")
            for error in errors:
                print(f"  - {error}")
            return 1
        print("OK: database is internally consistent.")
        return 0

    if args.command == "rebuild-index":
        data = db.rebuild_index()
        print(f"Wrote {config.index_file} ({len(data['position_to_expanded_tags'])} positions).")
        return 0

    if args.command == "clean":
        from .storage import iter_position_ids
        position_ids = args.position_ids or iter_position_ids(config)
        total_actions = 0
        for pid in position_ids:
            actions = clean_position_files(config, pid, dry_run=args.dry_run)
            if actions:
                print(f"{pid}:")
                for action in actions:
                    print(f"  {action}")
                total_actions += len(actions)
        prefix = "Would perform" if args.dry_run else "Performed"
        print(f"{prefix} {total_actions} rename(s) across {len(position_ids)} position(s).")
        return 0

    if args.command in {"position", "pos"}:
        graph = db.tag_graph()
        pc = args.position_command
        if pc == "create":
            tags = _canonicalize_tags(graph, args.tag)
            create_position(config, args.position_id, args.sgf, args.image, args.description, tags, _meta_pairs(args.meta))
            print(f"Created position {args.position_id}")
            return 0

        rec = load_position(config, args.position_id)
        if pc == "show":
            sgf, image = _entry_paths(config, args.position_id)
            payload = {
                "id": args.position_id,
                **rec,
                "sgf": str(sgf) if sgf else None,
                "image": str(image) if image else None,
            }
            if args.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).rstrip())
            return 0
        if pc == "add-tag":
            existing = _canonicalize_tags(graph, rec["tags"])
            for tag in _canonicalize_tags(graph, args.tags):
                if tag not in existing:
                    existing.append(tag)
            rec["tags"] = existing
            save_position(config, args.position_id, rec)
            print(f"Tags for {args.position_id}: {', '.join(existing) or '(none)'}")
            return 0
        if pc == "remove-tag":
            remove = {graph.normalize(graph.canonical(t)) for t in args.tags}
            rec["tags"] = [t for t in rec["tags"] if graph.normalize(t) not in remove]
            save_position(config, args.position_id, rec)
            print(f"Tags for {args.position_id}: {', '.join(rec['tags']) or '(none)'}")
            return 0
        if pc == "set-tags":
            rec["tags"] = _canonicalize_tags(graph, args.tags)
            save_position(config, args.position_id, rec)
            print(f"Tags for {args.position_id}: {', '.join(rec['tags']) or '(none)'}")
            return 0
        if pc == "set-description":
            rec["description"] = args.description
            save_position(config, args.position_id, rec)
            print(f"Updated description for {args.position_id}")
            return 0
        if pc == "meta-set":
            set_nested(rec["metadata"], args.key, parse_cli_value(args.value))
            save_position(config, args.position_id, rec)
            print(f"Set metadata {args.key} for {args.position_id}")
            return 0
        if pc == "meta-delete":
            delete_nested(rec["metadata"], args.key)
            save_position(config, args.position_id, rec)
            print(f"Deleted metadata {args.key} from {args.position_id}")
            return 0
        if pc == "meta-show":
            value = rec["metadata"] if args.key is None else get_nested(rec["metadata"], args.key)
            print(yaml.safe_dump(value, sort_keys=False, allow_unicode=True).rstrip())
            return 0

    if args.command in {"tag", "tags"}:
        graph = db.tag_graph()
        tc = args.tag_command
        if tc == "list":
            lines = graph.tree_lines() if args.tree else graph.names()
            for line in lines:
                print(line)
            return 0
        if tc == "add":
            graph.add(args.name, args.parent, args.description)
            print(f"Added tag {args.name}")
            return 0
        if tc == "remove":
            # Don't silently orphan position references.
            refs = []
            canonical = graph.canonical(args.name)
            for pid, rec in db.all_positions().items():
                if any(graph.normalize(t) == graph.normalize(canonical) for t in rec["tags"]):
                    refs.append(pid)
            if refs:
                raise DatabaseError(f"Tag '{canonical}' is still explicitly used by positions: {', '.join(refs)}")
            graph.remove(args.name, args.force)
            print(f"Removed tag {canonical}")
            return 0
        if tc == "add-parent":
            graph.add_parent(args.name, args.parent)
            print(f"Added parent {args.parent} -> {args.name}")
            return 0
        if tc == "remove-parent":
            graph.remove_parent(args.name, args.parent)
            print(f"Removed parent {args.parent} -> {args.name}")
            return 0

    raise AssertionError("unhandled command")


def run() -> None:
    try:
        raise SystemExit(main())
    except DatabaseError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    run()
