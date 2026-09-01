from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import tempfile
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = Path(os.environ.get("GO_POSITION_DB_ROOT", PROJECT_ROOT))
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass(frozen=True)
class KataGoConfig:
    executable: Path | None = None
    model: Path | None = None
    analysis_config: Path | None = None
    timeout_seconds: float = 60.0
    startup_timeout_seconds: float = 180.0
    report_interval_seconds: float = 0.1
    overlay_top_moves: int = 5
    overlay_max_point_loss: float = 2.0
    root_policy_temperature: float = 1.1
    num_analysis_threads: int = 1
    num_search_threads: int = 16
    nn_cache_size_power_of_two: int = 20


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
    katago: KataGoConfig = field(default_factory=KataGoConfig)

    @classmethod
    def defaults(cls, root: Path) -> "Config":
        return cls(
            root=root,
            positions_dir=root / "positions",
            tags_file=root / "tags.yaml",
            index_file=root / "generated" / "tag_index.yaml",
        )


def _resolve(root: Path, value: str | Path) -> Path:
    p = Path(os.path.expandvars(str(value))).expanduser()
    return p if p.is_absolute() else root / p


def _optional_path(root: Path, value: Any) -> Path | None:
    if value is None or not str(value).strip():
        return None
    return _resolve(root, str(value).strip()).resolve()


def load_config(root: Path | None = None, config_path: Path | None = None) -> Config:
    root = Path(root or DEFAULT_ROOT)
    # Application configuration lives with the code, independently from the
    # selected database root. An explicit --config path remains supported.
    cfg_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
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
    katago = data.get("katago", {}) or {}
    if not isinstance(katago, dict):
        raise ValueError("config.yaml 'katago' must be a mapping.")
    timeout_seconds = katago.get("timeout_seconds", 60.0)
    startup_timeout_seconds = katago.get("startup_timeout_seconds", 180.0)
    report_interval_seconds = katago.get("report_interval_seconds", 0.1)
    overlay_top_moves = katago.get("overlay_top_moves", 5)
    overlay_max_point_loss = katago.get("overlay_max_point_loss", 2.0)
    root_policy_temperature = katago.get("root_policy_temperature", 1.1)
    num_analysis_threads = katago.get("num_analysis_threads", 1)
    num_search_threads = katago.get("num_search_threads", 16)
    nn_cache_size_power_of_two = katago.get("nn_cache_size_power_of_two", 20)
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        raise ValueError("KataGo timeout_seconds must be a positive number.")
    if (
        not isinstance(startup_timeout_seconds, (int, float))
        or isinstance(startup_timeout_seconds, bool)
        or startup_timeout_seconds <= 0
    ):
        raise ValueError("KataGo startup_timeout_seconds must be a positive number.")
    if (
        not isinstance(report_interval_seconds, (int, float))
        or isinstance(report_interval_seconds, bool)
        or not 0.1 <= report_interval_seconds <= 10
    ):
        raise ValueError(
            "KataGo report_interval_seconds must be a number from 0.1 through 10."
        )
    integer_settings = (
        ("overlay_top_moves", overlay_top_moves, 1, 50),
        ("num_analysis_threads", num_analysis_threads, 1, 64),
        ("num_search_threads", num_search_threads, 1, 512),
        ("nn_cache_size_power_of_two", nn_cache_size_power_of_two, 10, 30),
    )
    for name, value, minimum, maximum in integer_settings:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not minimum <= value <= maximum
        ):
            raise ValueError(
                f"KataGo {name} must be an integer from {minimum} through {maximum}."
            )
    if (
        not isinstance(overlay_max_point_loss, (int, float))
        or isinstance(overlay_max_point_loss, bool)
        or not 0 <= overlay_max_point_loss <= 100
    ):
        raise ValueError(
            "KataGo overlay_max_point_loss must be a number from 0 through 100."
        )
    if (
        not isinstance(root_policy_temperature, (int, float))
        or isinstance(root_policy_temperature, bool)
        or not 0.1 <= root_policy_temperature <= 10
    ):
        raise ValueError(
            "KataGo root_policy_temperature must be a number from 0.1 through 10."
        )
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
        katago=KataGoConfig(
            # Engine files are application dependencies, so relative paths are
            # interpreted beside the application config rather than in a collection.
            executable=_optional_path(cfg_path.parent, katago.get("executable")),
            model=_optional_path(cfg_path.parent, katago.get("model")),
            analysis_config=_optional_path(cfg_path.parent, katago.get("analysis_config")),
            timeout_seconds=float(timeout_seconds),
            startup_timeout_seconds=float(startup_timeout_seconds),
            report_interval_seconds=float(report_interval_seconds),
            overlay_top_moves=overlay_top_moves,
            overlay_max_point_loss=float(overlay_max_point_loss),
            root_policy_temperature=float(root_policy_temperature),
            num_analysis_threads=num_analysis_threads,
            num_search_threads=num_search_threads,
            nn_cache_size_power_of_two=nn_cache_size_power_of_two,
        ),
    )


def _portable_config_path(path: Path | None, config_directory: Path) -> str:
    if path is None:
        return ""
    resolved = path.expanduser().resolve()
    try:
        return Path(os.path.relpath(resolved, config_directory.resolve())).as_posix()
    except ValueError:
        # Windows paths on different drives cannot be made relative.
        return str(resolved)


def save_katago_config(
    config: KataGoConfig,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> None:
    """Update only KataGo settings while preserving the rest of config.yaml."""
    config_path = Path(config_path)
    data: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as stream:
            loaded = yaml.safe_load(stream) or {}
        if not isinstance(loaded, dict):
            raise ValueError("config.yaml must contain a mapping.")
        data = loaded

    katago = data.get("katago") or {}
    if not isinstance(katago, dict):
        raise ValueError("config.yaml 'katago' must be a mapping.")
    katago.update({
        "executable": _portable_config_path(config.executable, config_path.parent),
        "model": _portable_config_path(config.model, config_path.parent),
        "analysis_config": _portable_config_path(
            config.analysis_config, config_path.parent
        ),
        "timeout_seconds": config.timeout_seconds,
        "startup_timeout_seconds": config.startup_timeout_seconds,
        "report_interval_seconds": config.report_interval_seconds,
        "overlay_top_moves": config.overlay_top_moves,
        "overlay_max_point_loss": config.overlay_max_point_loss,
        "root_policy_temperature": config.root_policy_temperature,
        "num_analysis_threads": config.num_analysis_threads,
        "num_search_threads": config.num_search_threads,
        "nn_cache_size_power_of_two": config.nn_cache_size_power_of_two,
    })
    # Continuous interactive analysis supersedes the old fixed visit limit.
    katago.pop("max_visits", None)
    data["katago"] = katago

    config_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{config_path.name}.", suffix=".tmp", dir=config_path.parent
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as stream:
            yaml.safe_dump(data, stream, sort_keys=False, allow_unicode=True)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary_name).replace(config_path)
    except Exception:
        try:
            Path(temporary_name).unlink()
        except FileNotFoundError:
            pass
        raise


def default_config_yaml() -> str:
    return r'''# Collection paths are relative to the selected database root.
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

# Optional local KataGo analysis engine. These paths are relative to this
# application config file. KataGo, a model, and an analysis config are not bundled.
katago:
  executable: ""
  model: ""
  analysis_config: ""
  # Analysis continues until Stop or the displayed position changes. This is
  # the maximum silence allowed between engine responses, not a search limit.
  timeout_seconds: 60
  startup_timeout_seconds: 180
  report_interval_seconds: 0.1
  overlay_top_moves: 5
  overlay_max_point_loss: 2.0
  root_policy_temperature: 1.1
  num_analysis_threads: 1
  num_search_threads: 16
  nn_cache_size_power_of_two: 20

'''
