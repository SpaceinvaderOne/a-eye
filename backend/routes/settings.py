from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.config import update_settings as config_update_settings, get_settings
from backend.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

router = APIRouter()

# Settings that can be changed at runtime via the UI
_MUTABLE_SETTINGS = {
    "rename_mode", "confidence_threshold", "filename_template",
    "max_filename_len", "filename_case", "use_exif_date", "use_gps",
    "gps_detail", "concurrent_workers", "watch_mode",
    "vision_model", "llm_model", "output_language", "ollama_host", "process_subdirs",
    "thumbnail_max_size", "thumbnail_quality", "thumbnail_retain_days",
    "dry_run",
    "excluded_folders",
    "catalogue_mode",
    "process_rename", "process_write_description", "process_write_tags",
    "basic_auth_user", "basic_auth_pass", "setup_complete",
    "schedule_enabled", "schedule_start", "schedule_end",
    "workspace_dir",
    "destructive_mode_library", "destructive_mode_workspace",
    "dashboard_showcase", "dashboard_showcase_tag", "dashboard_showcase_interval",
    "dashboard_showcase_kenburns",
    "dashboard_mosaic_speed",
    "dashboard_crossfade_speed",
}

# Keys that require recreating the Ollama client
_OLLAMA_KEYS = {"ollama_host", "vision_model", "llm_model"}


class SettingsUpdate(BaseModel):
    settings: dict[str, str]


@router.get("/settings")
async def get_settings_route(request: Request):
    """Current settings (from config.json + env defaults)."""
    settings = request.app.state.settings

    # Keys to redact from GET response (write-only)
    _REDACTED = {"basic_auth_pass"}

    current = {}
    for key in _MUTABLE_SETTINGS:
        if key in _REDACTED:
            value = getattr(settings, key, "")
            current[key] = "********" if value else ""
        else:
            current[key] = str(getattr(settings, key, ""))

    return {"settings": current}


async def _apply_settings_update(request: Request, body: SettingsUpdate):
    """Shared logic for applying a settings update."""
    from pathlib import Path

    # Filter to only mutable keys; ignore redacted placeholder values
    updates = {k: v for k, v in body.settings.items() if k in _MUTABLE_SETTINGS}
    if updates.get("basic_auth_pass") == "********":
        del updates["basic_auth_pass"]
    if not updates:
        return {"updated": []}

    # Validate workspace_dir stays within data_dir
    if "workspace_dir" in updates and updates["workspace_dir"]:
        data_dir = Path(request.app.state.settings.data_dir).resolve()
        try:
            ws_path = Path(updates["workspace_dir"]).resolve()
            ws_path.relative_to(data_dir)
        except ValueError:
            raise HTTPException(400, "workspace_dir must be inside data_dir")

    # Check if Ollama-related settings are changing
    ollama_changed = bool(set(updates.keys()) & _OLLAMA_KEYS)

    # Write to config.json and reload the Settings object
    new_settings = config_update_settings(updates)

    # Update the live app state
    request.app.state.settings = new_settings

    # Update worker, watcher, scheduler, and workspace references
    request.app.state.worker.settings = new_settings
    request.app.state.watcher.settings = new_settings
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        scheduler.settings = new_settings
    workspace = getattr(request.app.state, "workspace", None)
    if workspace:
        workspace.settings = new_settings

    # Recreate Ollama client if connection settings changed
    if ollama_changed:
        old_ollama = request.app.state.ollama
        new_ollama = OllamaClient(
            host=new_settings.ollama_host,
            vision_model=new_settings.vision_model,
            llm_model=new_settings.llm_model,
        )
        # Carry over prompt templates from the old client
        new_ollama.set_templates(old_ollama._vision_template, old_ollama._context_template)
        request.app.state.ollama = new_ollama
        request.app.state.worker.ollama = new_ollama
        if workspace:
            workspace.ollama = new_ollama
        await old_ollama.close()
        logger.info(
            "Recreated Ollama client — host=%s, vision=%s, llm=%s",
            new_settings.ollama_host, new_settings.vision_model, new_settings.llm_model,
        )

    logger.info("Settings updated via UI: %s", list(updates.keys()))
    return {"updated": list(updates.keys())}


@router.put("/settings")
async def update_settings_route(request: Request, body: SettingsUpdate):
    """Update settings — writes to config.json and reloads live state."""
    return await _apply_settings_update(request, body)


@router.put("/onboard/settings")
async def onboard_settings_route(request: Request, body: SettingsUpdate):
    """Settings endpoint for onboarding wizard — only works before setup is complete."""
    settings = request.app.state.settings
    if settings.setup_complete:
        raise HTTPException(403, "Setup already complete")
    return await _apply_settings_update(request, body)
