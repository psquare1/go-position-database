from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = Path(os.environ.get("GO_POSITION_DB_ROOT", PROJECT_ROOT))


@dataclass(frozen=True)
class Config:
    root: Path
    positions_dir: Path
    tags_file: Path
    index_file: Path
    sgf_filename: str = "position.sgf"
    image_filename: str = "position.png"
    metadata_filename: str = "metadata.yaml"
    sgf_extensions: tuple[str, ...] = (".sgf",)
    image_extensions: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")

    @classmethod
    def defaults(cls, root: Path) -> "Config":
        return cls(
            root=root,
            positions_dir=root / "positions",
            tags_file=root / "tags.yaml",
            index_file=root / "generated" / "tag_index.yaml",
        )


def _resolve(root: Path, value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


def load_config(root: Path | None = None, config_path: Path | None = None) -> Config:
    root = Path(root or DEFAULT_ROOT)
    cfg_path = Path(config_path) if config_path else root / "config.yaml"
    defaults = Config.defaults(root)

    if not cfg_path.exists():
        return defaults

    with cfg_path.open("r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    # config.yaml can itself move the database root. CLI --root remains authoritative.
    if root == DEFAULT_ROOT and data.get("root"):
        configured_root = Path(data["root"])
        root = configured_root
        defaults = Config.defaults(root)

    files = data.get("files", {}) or {}
    return Config(
        root=root,
        positions_dir=_resolve(root, data.get("positions_directory", "positions")),
        tags_file=_resolve(root, data.get("tags_file", "tags.yaml")),
        index_file=_resolve(root, data.get("generated_index", "generated/tag_index.yaml")),
        sgf_filename=files.get("sgf", defaults.sgf_filename),
        image_filename=files.get("image", defaults.image_filename),
        metadata_filename=files.get("metadata", defaults.metadata_filename),
        sgf_extensions=tuple(str(x).lower() for x in files.get("sgf_extensions", defaults.sgf_extensions)),
        image_extensions=tuple(str(x).lower() for x in files.get("image_extensions", defaults.image_extensions)),
    )


def default_config_yaml() -> str:
    return r'''# All paths below are relative to this database root.
positions_directory: positions
tags_file: tags.yaml
generated_index: generated/tag_index.yaml

files:
  sgf: position.sgf
  image: position.png
  metadata: metadata.yaml
  # Used for auto-detection when files have arbitrary names.
  sgf_extensions: [.sgf]
  image_extensions: [.png, .jpg, .jpeg, .webp, .bmp, .gif]

'''
