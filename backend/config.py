from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "config.json"

# Module-level settings (mutable — no lru_cache)
_settings: Settings | None = None


class Settings(BaseSettings):
    # -- Ollama Connection --
    ollama_host: str = "http://localhost:11434"

    # -- Model Selection --
    vision_model: str = "minicpm-v"
    llm_model: str = ""  # Text model for search (optional)
    output_language: str = ""

    # -- Paths --
    photos_dir: str = "/photos"
    data_dir: str = "/app/data"

    # -- Web UI --
    port: int = 8000
    basic_auth_user: str = ""
    basic_auth_pass: str = ""
    setup_complete: bool = False

    # -- Rename Mode --
    rename_mode: str = "review"  # review | auto | auto-low-confidence
    confidence_threshold: float = 0.6
    watch_mode: bool = False
    dry_run: bool = False

    # -- Naming --
    filename_template: str = "{description}_{location}_{date}"
    max_filename_len: int = 120
    filename_case: str = "lower"  # lower | title | original

    # -- Metadata --
    use_exif_date: bool = True
    use_gps: bool = True
    gps_detail: str = "city"  # city | city-country | full | coordinates

    # -- Processing Modes --
    catalogue_mode: bool = False  # When True, skip all disk writes (rename + XMP)
    process_rename: bool = True
    process_write_description: bool = False
    process_write_tags: bool = False

    # -- Schedule --
    schedule_enabled: bool = False
    schedule_start: str = "22:00"  # HH:MM 24h format
    schedule_end: str = "06:00"    # HH:MM 24h format

    # -- Processing --
    concurrent_workers: int = 1
    skip_processed: bool = True
    process_subdirs: bool = True
    excluded_folders: str = ""  # JSON array of relative folder paths, e.g. '["2024/old","temp"]'
    max_upload_size_mb: int = 200  # Per-file upload size limit in MB (0 = unlimited)

    # -- Workspace --
    workspace_dir: str = ""  # Empty = default to data_dir/workspace

    # -- Destructive Mode --
    destructive_mode_library: bool = False
    destructive_mode_workspace: bool = True

    # -- Dashboard --
    dashboard_showcase: bool = False
    dashboard_showcase_tag: str = ""
    dashboard_showcase_interval: int = 15  # seconds between each photo swap
    dashboard_showcase_kenburns: bool = True  # slow zoom & pan effect
    dashboard_mosaic_speed: int = 3  # multiplier: mosaic changes N times faster than showcase
    dashboard_crossfade_speed: float = 2.0  # seconds: 1=fast, 2=medium, 4=slow

    # -- Thumbnails --
    thumbnail_max_size: int = 400
    thumbnail_quality: int = 80
    thumbnail_retain_days: int = 30  # 0 = never auto-prune

    @field_validator("ollama_host", mode="before")
    @classmethod
    def _validate_ollama_host(cls, v: str) -> str:
        v = str(v).strip().rstrip("/")
        if v and not v.startswith(("http://", "https://")):
            v = "http://" + v
        return v

    @field_validator("rename_mode", mode="before")
    @classmethod
    def _validate_rename_mode(cls, v: str) -> str:
        valid = {"review", "auto", "auto-low-confidence"}
        if v not in valid:
            logger.warning("Invalid rename_mode '%s', defaulting to 'review'", v)
            return "review"
        return v

    @field_validator("filename_case", mode="before")
    @classmethod
    def _validate_filename_case(cls, v: str) -> str:
        valid = {"lower", "title", "original"}
        if v not in valid:
            logger.warning("Invalid filename_case '%s', defaulting to 'lower'", v)
            return "lower"
        return v

    @field_validator("gps_detail", mode="before")
    @classmethod
    def _validate_gps_detail(cls, v: str) -> str:
        valid = {"city", "city-country", "full", "coordinates"}
        if v not in valid:
            logger.warning("Invalid gps_detail '%s', defaulting to 'city'", v)
            return "city"
        return v

    @field_validator("schedule_start", "schedule_end", mode="before")
    @classmethod
    def _validate_schedule_time(cls, v: str) -> str:
        v = str(v).strip()
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            logger.warning("Invalid schedule time '%s', defaulting to '00:00'", v)
            return "00:00"
        return v

    @field_validator("concurrent_workers", mode="before")
    @classmethod
    def _validate_concurrent_workers(cls, v: Any) -> int:
        try:
            v = int(v)
        except (ValueError, TypeError):
            return 1
        if v < 1:
            return 1
        if v > 4:
            logger.warning("concurrent_workers %d clamped to 4", v)
            return 4
        return v

    @property
    def excluded_folders_set(self) -> set[str]:
        """Parse excluded_folders JSON string into a set of normalized relative paths."""
        if not self.excluded_folders:
            return set()
        try:
            folders = json.loads(self.excluded_folders)
            if isinstance(folders, list):
                return {f.strip("/") for f in folders if isinstance(f, str) and f.strip("/")}
            return set()
        except (json.JSONDecodeError, TypeError):
            return set()

    model_config = {"env_file": ".env", "extra": "ignore"}


# -- Config file persistence ------------------------------------------------
# Priority: config.json > env vars > hardcoded defaults
# Env vars are for initial container setup.
# Once the user saves settings via the UI, config.json takes over.


def _config_path(data_dir: str) -> Path:
    return Path(data_dir) / _CONFIG_FILENAME


def load_config_file(data_dir: str) -> dict[str, Any]:
    """Read config.json from the data directory. Returns empty dict if missing."""
    path = _config_path(data_dir)
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        logger.info("Loaded config overrides from %s (%d keys)", path, len(data))
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return {}


def save_config_file(data_dir: str, overrides: dict[str, Any]) -> None:
    """Write config overrides to config.json."""
    path = _config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(overrides, f, indent=2, sort_keys=True)
    logger.info("Saved config to %s (%d keys)", path, len(overrides))


def get_settings() -> Settings:
    """Get the current Settings instance.

    On first call, builds Settings from env vars + config.json overlay.
    Subsequent calls return the cached instance (use reload_settings() to refresh).
    """
    global _settings
    if _settings is None:
        _settings = _build_settings()
    return _settings


def reload_settings() -> Settings:
    """Re-read config.json and rebuild the Settings object."""
    global _settings
    _settings = _build_settings()
    return _settings


def update_settings(updates: dict[str, str]) -> Settings:
    """Merge updates into config.json, save, and reload.

    Only updates keys that are actual Settings fields.
    """
    # Get current data_dir from the existing settings (or env default)
    current = get_settings()
    data_dir = current.data_dir

    # Load existing overrides, merge in new values
    overrides = load_config_file(data_dir)
    valid_fields = set(Settings.model_fields.keys())
    for key, value in updates.items():
        if key in valid_fields:
            overrides[key] = value

    save_config_file(data_dir, overrides)
    return reload_settings()


def _build_settings() -> Settings:
    """Build a Settings object: env vars first, then config.json overlay."""
    # Step 1: Load from env vars (Pydantic's default behaviour)
    env_settings = Settings()

    # Step 2: Load config.json overrides
    overrides = load_config_file(env_settings.data_dir)
    if not overrides:
        return env_settings

    # Step 3: Merge — config.json values override env values
    # We need to coerce string values from JSON to the right types
    merged = {}
    for field_name, field_info in Settings.model_fields.items():
        if field_name in overrides:
            merged[field_name] = _coerce(overrides[field_name], field_info.annotation)
        else:
            merged[field_name] = getattr(env_settings, field_name)

    # Build a new Settings with the merged values, skipping env/file validation
    return Settings(**merged)


def _coerce(value: Any, annotation: Any) -> Any:
    """Coerce a JSON value to the expected Python type."""
    if annotation is bool or annotation == bool:
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "yes")
    if annotation is int or annotation == int:
        try:
            return int(value)
        except (ValueError, TypeError):
            return value
    if annotation is float or annotation == float:
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
    return value
