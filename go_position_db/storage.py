from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable

import yaml

from .config import Config

RESERVED_POSITION_KEYS = {
    "description", "score", "score_visits", "main_media_kind", "sgf_start_path",
    "tags", "metadata", "solution_images",
}


class DatabaseError(RuntimeError):
    pass


def load_yaml(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        value = yaml.safe_load(f)
    return default if value is None else value


def atomic_dump_yaml(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with open(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(value, f, sort_keys=False, allow_unicode=True)
        Path(tmp_name).replace(path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def position_dir(config: Config, position_id: str) -> Path:
    return config.positions_dir / position_id


def position_metadata_path(config: Config, position_id: str) -> Path:
    return position_dir(config, position_id) / config.metadata_filename


def _matching_files(directory: Path, extensions: Iterable[str]) -> list[Path]:
    extset = {str(ext).lower() if str(ext).startswith(".") else "." + str(ext).lower() for ext in extensions}
    return sorted(
        (p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in extset),
        key=lambda p: p.name.casefold(),
    ) if directory.exists() else []


def _resolve_entry_file(
    directory: Path,
    preferred_name: str,
    extensions: Iterable[str],
    kind: str,
) -> Path | None:
    preferred = directory / preferred_name
    matches = _matching_files(directory, extensions)
    if preferred.exists():
        return preferred
    if not matches:
        return None
    if len(matches) > 1:
        names = ", ".join(p.name for p in matches)
        raise DatabaseError(f"Ambiguous {kind} files in {directory}: {names}")
    return matches[0]


def position_sgf_path(config: Config, position_id: str) -> Path | None:
    return _resolve_entry_file(
        position_dir(config, position_id), config.sgf_filename, config.sgf_extensions, "SGF"
    )


def position_image_path(config: Config, position_id: str) -> Path | None:
    return _resolve_entry_file(
        position_dir(config, position_id), config.image_filename, config.image_extensions, "image"
    )


def entry_file_issues(config: Config, position_id: str) -> list[str]:
    """Return ambiguity/duplicate warnings without requiring canonical filenames."""
    d = position_dir(config, position_id)
    issues: list[str] = []
    for kind, preferred_name, extensions in (
        ("SGF", config.sgf_filename, config.sgf_extensions),
        ("image", config.image_filename, config.image_extensions),
    ):
        matches = _matching_files(d, extensions)
        preferred = d / preferred_name
        # A preferred file is deterministic, but extra files of the same kind are probably accidental.
        if preferred.exists():
            extras = [p for p in matches if p != preferred]
            if extras:
                issues.append(f"{position_id}: multiple {kind} files: " + ", ".join(p.name for p in matches))
        elif len(matches) > 1:
            issues.append(f"{position_id}: ambiguous {kind} files: " + ", ".join(p.name for p in matches))
    return issues


def clean_position_files(config: Config, position_id: str, dry_run: bool = False) -> list[str]:
    """Normalize arbitrary SGF/image names while preserving the image's real extension."""
    d = position_dir(config, position_id)
    if not d.exists():
        raise DatabaseError(f"No entry '{position_id}' ({d} does not exist).")

    actions: list[str] = []

    # SGF can always be renamed to the configured canonical SGF filename.
    sgf_matches = _matching_files(d, config.sgf_extensions)
    sgf_target = d / config.sgf_filename
    if sgf_target.exists():
        extras = [p for p in sgf_matches if p != sgf_target]
        if extras:
            raise DatabaseError(
                f"Cannot clean {position_id}: multiple SGF files: " + ", ".join(p.name for p in sgf_matches)
            )
    elif len(sgf_matches) == 1:
        source = sgf_matches[0]
        actions.append(f"{source.name} -> {sgf_target.name}")
        if not dry_run:
            source.rename(sgf_target)
    elif len(sgf_matches) > 1:
        raise DatabaseError(
            f"Cannot clean {position_id}: ambiguous SGF files: " + ", ".join(p.name for p in sgf_matches)
        )

    # Preserve the actual image extension: foo.jpg -> position.jpg, never merely rename JPG bytes to .png.
    image_matches = _matching_files(d, config.image_extensions)
    preferred_image = d / config.image_filename
    if preferred_image.exists():
        extras = [p for p in image_matches if p != preferred_image]
        if extras:
            raise DatabaseError(
                f"Cannot clean {position_id}: multiple image files: " + ", ".join(p.name for p in image_matches)
            )
    elif len(image_matches) == 1:
        source = image_matches[0]
        canonical_stem = Path(config.image_filename).stem
        target = d / f"{canonical_stem}{source.suffix.lower()}"
        if source != target:
            if target.exists():
                raise DatabaseError(f"Cannot clean {position_id}: target already exists: {target.name}")
            actions.append(f"{source.name} -> {target.name}")
            if not dry_run:
                source.rename(target)
    elif len(image_matches) > 1:
        raise DatabaseError(
            f"Cannot clean {position_id}: ambiguous image files: " + ", ".join(p.name for p in image_matches)
        )

    return actions


SCORE_PATTERN = re.compile(r"^\s*([BW])\s*\+\s*(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)
NUMERIC_SCORE_PATTERN = re.compile(r"^\s*([+-]?)(\d+(?:\.\d+)?)\s*$")


def formatted_score(value: Any) -> str | None:
    """Return a canonical display score such as ``B +3.5``, or None if invalid."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    match = SCORE_PATTERN.fullmatch(value)
    if match:
        return f"{match.group(1).upper()} +{match.group(2)}"
    numeric = NUMERIC_SCORE_PATTERN.fullmatch(value)
    if not numeric or float(numeric.group(2)) == 0:
        return None
    player = "W" if numeric.group(1) == "-" else "B"
    return f"{player} +{numeric.group(2)}"


def normalized_sgf_start_path(value: Any, label: str = "SGF start path") -> list[int]:
    """Validate a stable SGF node path expressed as child indexes from the root."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(index, int) and not isinstance(index, bool) and index >= 0
        for index in value
    ):
        raise DatabaseError(f"{label} must be a list of non-negative child indexes.")
    return list(value)


def normalized_score_visits(value: Any, label: str = "Score visits") -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DatabaseError(f"{label} must be a non-negative integer.")
    return value


def normalize_position_record(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise DatabaseError("Entry metadata must be a YAML mapping.")
    result = dict(value)
    # Position folders are the user-facing identifiers; discard the retired name field.
    result.pop("name", None)
    result.setdefault("description", "")
    result.setdefault("score", "")
    result.setdefault("score_visits", 0)
    result.setdefault("main_media_kind", "board")
    result.setdefault("sgf_start_path", [])
    result.setdefault("tags", [])
    result.setdefault("metadata", {})
    result.setdefault("solution_images", [])
    if not isinstance(result["description"], str):
        raise DatabaseError("Entry 'description' must be a string.")
    normalized_score = formatted_score(result["score"])
    if normalized_score:
        result["score"] = normalized_score
    elif not isinstance(result["score"], str):
        raise DatabaseError("Entry 'score' must be a score string or a positive/negative number.")
    result["score_visits"] = normalized_score_visits(
        result["score_visits"], "Entry 'score_visits'"
    )
    if result["main_media_kind"] not in {"board", "image"}:
        raise DatabaseError("Entry 'main_media_kind' must be 'board' or 'image'.")
    result["sgf_start_path"] = normalized_sgf_start_path(
        result["sgf_start_path"], "Entry 'sgf_start_path'"
    )
    if not isinstance(result["tags"], list) or not all(isinstance(x, str) for x in result["tags"]):
        raise DatabaseError("Entry 'tags' must be a list of strings.")
    # Import lazily to keep storage and tag-graph module initialization independent.
    from .tags import validate_new_tag_name
    result["tags"] = [validate_new_tag_name(tag) for tag in result["tags"]]
    if not isinstance(result["metadata"], dict):
        raise DatabaseError("Entry 'metadata' must be a mapping.")
    if not isinstance(result["solution_images"], list):
        raise DatabaseError("Entry 'solution_images' must be a list.")
    normalized_solutions: list[dict[str, Any]] = []
    for index, item in enumerate(result["solution_images"], start=1):
        if not isinstance(item, dict):
            raise DatabaseError(f"Variation {index} must be a mapping.")
        kind = item.get("kind", "image")
        file_value = item.get("file", "")
        description = item.get("description", "")
        score = item.get("score", "")
        score_visits = normalized_score_visits(
            item.get("score_visits", 0), f"Variation {index} 'score_visits'"
        )
        sgf_start_path = normalized_sgf_start_path(
            item.get("sgf_start_path", []), f"Variation {index} 'sgf_start_path'"
        )
        if kind not in {"image", "board"}:
            raise DatabaseError(f"Variation {index} kind must be 'image' or 'board'.")
        if not isinstance(file_value, str):
            raise DatabaseError(f"Variation {index} file path must be a string.")
        if kind == "image" and not file_value.strip():
            raise DatabaseError(f"Variation {index} needs a file path.")
        relative = Path(file_value) if file_value else None
        if relative is not None and (relative.is_absolute() or ".." in relative.parts):
            raise DatabaseError(f"Variation {index} file path must stay inside the entry folder.")
        if not isinstance(description, str):
            raise DatabaseError(f"Variation {index} description must be a string.")
        normalized_solution_score = formatted_score(score)
        if normalized_solution_score:
            score = normalized_solution_score
        elif not isinstance(score, str):
            raise DatabaseError(f"Variation {index} score must be a score string or a positive/negative number.")
        normalized_solutions.append({
            "kind": kind,
            "file": relative.as_posix() if relative is not None else "",
            "description": description,
            "score": score,
            "score_visits": score_visits,
            "sgf_start_path": sgf_start_path,
        })
    result["solution_images"] = normalized_solutions
    return result


def load_position(config: Config, position_id: str) -> dict[str, Any]:
    path = position_metadata_path(config, position_id)
    if not path.exists():
        raise DatabaseError(f"No entry '{position_id}' ({path} does not exist).")
    return normalize_position_record(load_yaml(path, {}))


def save_position(config: Config, position_id: str, record: dict[str, Any]) -> None:
    atomic_dump_yaml(position_metadata_path(config, position_id), normalize_position_record(record))


def iter_position_ids(config: Config) -> list[str]:
    if not config.positions_dir.exists():
        return []
    return sorted(p.name for p in config.positions_dir.iterdir() if p.is_dir())


def parse_cli_value(text: str) -> Any:
    """Parse JSON values when possible; otherwise preserve the input as a string."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def set_nested(mapping: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = [p for p in dotted_key.split(".") if p]
    if not parts:
        raise DatabaseError("Metadata key cannot be empty.")
    cur = mapping
    for part in parts[:-1]:
        child = cur.get(part)
        if child is None:
            child = {}
            cur[part] = child
        if not isinstance(child, dict):
            raise DatabaseError(f"Cannot descend through non-mapping metadata key '{part}'.")
        cur = child
    cur[parts[-1]] = value


def get_nested(mapping: dict[str, Any], dotted_key: str) -> Any:
    cur: Any = mapping
    for part in [p for p in dotted_key.split(".") if p]:
        if not isinstance(cur, dict) or part not in cur:
            raise DatabaseError(f"Metadata key '{dotted_key}' does not exist.")
        cur = cur[part]
    return cur


def delete_nested(mapping: dict[str, Any], dotted_key: str) -> None:
    parts = [p for p in dotted_key.split(".") if p]
    if not parts:
        raise DatabaseError("Metadata key cannot be empty.")
    cur = mapping
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            raise DatabaseError(f"Metadata key '{dotted_key}' does not exist.")
        cur = cur[part]
    if parts[-1] not in cur:
        raise DatabaseError(f"Metadata key '{dotted_key}' does not exist.")
    del cur[parts[-1]]


def create_position(
    config: Config,
    position_id: str,
    sgf: Path | None,
    image: Path | None,
    description: str,
    tags: Iterable[str],
    metadata: dict[str, Any] | None = None,
) -> None:
    dest = position_dir(config, position_id)
    if dest.exists():
        raise DatabaseError(f"Entry '{position_id}' already exists.")
    if sgf is None and image is None:
        raise DatabaseError("At least one of --sgf or --image must be provided.")
    if sgf is not None and not sgf.exists():
        raise DatabaseError(f"SGF file does not exist: {sgf}")
    if image is not None and not image.exists():
        raise DatabaseError(f"Image file does not exist: {image}")

    dest.mkdir(parents=True)
    if sgf is not None:
        shutil.copy2(sgf, dest / config.sgf_filename)
    if image is not None:
        # Keep the actual image extension unless it matches the configured canonical name's extension.
        image_target = Path(config.image_filename)
        if image.suffix.lower() != image_target.suffix.lower():
            image_target = image_target.with_suffix(image.suffix.lower())
        shutil.copy2(image, dest / image_target)
    save_position(config, position_id, {
        "description": description,
        "score": "",
        "main_media_kind": "board",
        "sgf_start_path": [],
        "tags": list(dict.fromkeys(tags)),
        "metadata": metadata or {},
        "solution_images": [],
    })
