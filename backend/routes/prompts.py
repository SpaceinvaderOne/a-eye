"""API endpoints for the Prompt Library."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from backend.prompts import (
    AI_HELPER_CONTEXT,
    AI_HELPER_VISION,
    STAGE_CONTEXT,
    STAGE_VISION,
    create_prompt as db_create_prompt,
    delete_prompt as db_delete_prompt,
    export_as_markdown,
    get_active_prompt,
    get_prompt,
    list_prompts,
    set_active,
    update_prompt as db_update_prompt,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prompts")


class PromptCreate(BaseModel):
    name: str
    stage: str
    content: str


class PromptUpdate(BaseModel):
    name: str | None = None
    content: str | None = None


@router.get("")
async def api_list_prompts(request: Request, stage: str | None = None):
    """List all prompts, optionally filtered by stage."""
    db = request.app.state.db
    prompts = await list_prompts(db, stage=stage)
    return {"prompts": prompts}


@router.get("/ai-helper")
async def api_ai_helper():
    """Get the AI helper prompts for creating custom prompts."""
    return {"vision": AI_HELPER_VISION, "context": AI_HELPER_CONTEXT}


@router.get("/{prompt_id}")
async def api_get_prompt(request: Request, prompt_id: int):
    """Get a single prompt by ID."""
    db = request.app.state.db
    prompt = await get_prompt(db, prompt_id)
    if not prompt:
        raise HTTPException(404, "Prompt not found")
    return prompt


@router.post("")
async def api_create_prompt(request: Request, body: PromptCreate):
    """Create a new custom prompt."""
    db = request.app.state.db
    try:
        prompt_id = await db_create_prompt(db, body.name, body.stage, body.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": prompt_id}


@router.put("/{prompt_id}")
async def api_update_prompt(request: Request, prompt_id: int, body: PromptUpdate):
    """Update a custom prompt."""
    db = request.app.state.db
    try:
        await db_update_prompt(db, prompt_id, name=body.name, content=body.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    await _reload_templates(request)

    return {"updated": True}


@router.delete("/{prompt_id}")
async def api_delete_prompt(request: Request, prompt_id: int):
    """Delete a custom prompt."""
    db = request.app.state.db
    try:
        await db_delete_prompt(db, prompt_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    await _reload_templates(request)

    return {"deleted": True}


@router.post("/{prompt_id}/activate")
async def api_activate_prompt(request: Request, prompt_id: int):
    """Set a prompt as active for its stage and reload templates."""
    db = request.app.state.db
    try:
        await set_active(db, prompt_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    # Reload templates on the OllamaClient
    await _reload_templates(request)

    return {"activated": True}


@router.get("/{prompt_id}/export")
async def api_export_prompt(request: Request, prompt_id: int):
    """Download a prompt as a markdown file."""
    db = request.app.state.db
    prompt = await get_prompt(db, prompt_id)
    if not prompt:
        raise HTTPException(404, "Prompt not found")

    md = export_as_markdown(prompt)
    safe_name = prompt["name"].replace(" ", "_").lower()[:40]
    safe_name = safe_name.replace('"', '').replace('\r', '').replace('\n', '')
    return PlainTextResponse(
        content=md,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.md"'},
    )


async def _reload_templates(request: Request) -> None:
    """Reload active prompt templates onto the OllamaClient."""
    db = request.app.state.db
    ollama = request.app.state.ollama

    vision = await get_active_prompt(db, STAGE_VISION)
    context = await get_active_prompt(db, STAGE_CONTEXT)

    vision_content = vision["content"] if vision else ""
    context_content = context["content"] if context else ""

    ollama.set_templates(vision_content, context_content)
    logger.info("Reloaded prompt templates on OllamaClient")

    # Also reload on workspace's ollama if it exists
    workspace = getattr(request.app.state, "workspace", None)
    if workspace and hasattr(workspace, "ollama"):
        workspace.ollama.set_templates(vision_content, context_content)
