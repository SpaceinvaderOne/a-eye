from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from backend.image_io import open_image

logger = logging.getLogger(__name__)

# Generous timeout for vision models — they can be slow on large images
_GENERATE_TIMEOUT = 300.0  # 5 minutes
_DEFAULT_TIMEOUT = 30.0

# Max dimension (longest side) for images sent to the vision model.
# Vision models resize internally to ~448-672px anyway, so sending anything
# larger than this wastes memory, bandwidth, and processing time.
_VISION_MAX_PX = 1280
_VISION_JPEG_QUALITY = 85

# Name substrings used to guess vision capability when /api/show fails.
_VISION_NAME_HINTS = ("llava", "minicpm-v", "moondream", "bakllava", "-vl", "vl:", "vision")


class OllamaClient:
    """Async wrapper for the Ollama HTTP API."""

    def __init__(self, host: str, vision_model: str, llm_model: str = "") -> None:
        self.host = host.rstrip("/")
        self.vision_model = vision_model
        self.llm_model = llm_model
        self._client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        # Prompt templates — set via set_templates() after startup
        self._vision_template: str = ""
        self._context_template: str = ""

    def set_templates(self, vision_template: str, context_template: str) -> None:
        """Set the active prompt templates (called on startup and when prompts change)."""
        self._vision_template = vision_template
        self._context_template = context_template

    async def close(self) -> None:
        await self._client.aclose()

    # -- Connection ----------------------------------------------------------

    async def check_connection(self) -> bool:
        """Return True if the Ollama server is reachable."""
        try:
            resp = await self._client.get(f"{self.host}/api/tags")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def list_models(self) -> list[dict[str, Any]]:
        """Return the list of models available on the Ollama server."""
        try:
            resp = await self._client.get(f"{self.host}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            return data.get("models", [])
        except httpx.HTTPError:
            logger.warning("Failed to list Ollama models", exc_info=True)
            return []

    async def get_model_capabilities(self, model_name: str) -> list[str] | None:
        """Fetch capabilities for a single model via /api/show. None = query failed."""
        try:
            resp = await self._client.post(
                f"{self.host}/api/show",
                json={"name": model_name},
            )
            resp.raise_for_status()
            return resp.json().get("capabilities", [])
        except httpx.HTTPError:
            logger.warning("Capability query failed for model %s", model_name, exc_info=True)
            return None

    async def list_models_by_capability(self) -> dict[str, list[str]]:
        """Return models grouped into vision-capable and text-only lists."""
        models = await self.list_models()
        names = [m.get("name", "") for m in models if m.get("name")]
        if not names:
            return {"vision": [], "text": [], "all": []}

        # Query capabilities for all models in parallel
        caps = await asyncio.gather(
            *(self.get_model_capabilities(n) for n in names)
        )

        vision = []
        text = []
        for name, model_caps in zip(names, caps):
            if model_caps is None:
                if any(h in name.lower() for h in _VISION_NAME_HINTS):
                    logger.warning("Using name-based vision detection for %s", name)
                    vision.append(name)
                else:
                    text.append(name)
            elif "vision" in model_caps:
                vision.append(name)
            else:
                text.append(name)

        return {"vision": vision, "text": text, "all": names}

    # -- Model Pull ----------------------------------------------------------

    async def pull_model_stream(self, model_name: str):
        """Stream pull progress from Ollama's /api/pull endpoint.
        Yields NDJSON lines with enriched percentage field.
        """
        import json as _json

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{self.host}/api/pull",
                json={"name": model_name, "stream": True},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = _json.loads(line)
                        if obj.get("total") and obj.get("completed") and "percentage" not in obj:
                            obj["percentage"] = round((obj["completed"] / obj["total"]) * 100)
                        yield _json.dumps(obj) + "\n"
                    except _json.JSONDecodeError:
                        yield line + "\n"

    # -- Generation ----------------------------------------------------------

    async def _generate(
        self,
        model: str,
        prompt: str,
        images: list[str] | None = None,
        options: dict | None = None,
    ) -> str:
        """Low-level generate call. Returns the response text."""
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        if images:
            payload["images"] = images
        if options:
            payload["options"] = options

        resp = await self._client.post(
            f"{self.host}/api/generate",
            json=payload,
            timeout=_GENERATE_TIMEOUT,
        )
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", "") or resp.text[:300]
            except Exception:
                detail = resp.text[:300]
            raise RuntimeError(f"Ollama error ({resp.status_code}): {detail}")
        data = resp.json()
        return data.get("response", "").strip()

    # -- Vision (describe + name) -------------------------------------------

    async def describe_and_name_image(
        self,
        image_path: Path,
        metadata: dict[str, Any],
        include_tags: bool = False,
        processing_context: str | None = None,
        output_language: str = "",
    ) -> tuple[str, str, list[str], list[str]]:
        """Single-model mode: vision model produces description, filename, tags, and quality flags.

        Returns (description, suggested_filename, tags, quality_flags).
        """
        from backend.prompts import render_vision_prompt, DEFAULT_VISION_PROMPT, DEFAULT_CONTEXT_TEMPLATE

        meta_lines = _format_metadata_for_prompt(metadata)

        vision_tmpl = self._vision_template or DEFAULT_VISION_PROMPT
        context_tmpl = self._context_template or DEFAULT_CONTEXT_TEMPLATE

        prompt = render_vision_prompt(
            vision_tmpl,
            context_tmpl,
            metadata_text=meta_lines,
            include_tags=include_tags,
            processing_context=processing_context,
            output_language=output_language,
        )

        image_b64 = _encode_image(image_path)
        raw = await self._generate(
            model=self.vision_model,
            prompt=prompt,
            images=[image_b64],
        )

        parsed = _parse_response(raw)
        return parsed["description"], parsed["filename"], parsed["tags"], parsed["quality_flags"]



# -- Helpers -----------------------------------------------------------------


def _encode_image(image_path: Path) -> str:
    """Downscale an image and return its base64-encoded JPEG content.

    Opens the original file via open_image() (supports JPEG, PNG, HEIC, RAW, etc.),
    resizes to fit within _VISION_MAX_PX on the longest side, and encodes as JPEG
    in memory. The original file is never modified.
    """
    img = open_image(image_path)

    # Only downscale — don't upscale small images
    if max(img.size) > _VISION_MAX_PX:
        img.thumbnail((_VISION_MAX_PX, _VISION_MAX_PX), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_VISION_JPEG_QUALITY)

    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _format_metadata_for_prompt(metadata: dict[str, Any]) -> str:
    """Format metadata dict into readable lines for the LLM prompt."""
    lines = ["Metadata:"]
    if metadata.get("date"):
        lines.append(f"- Date: {metadata['date']}")
    if metadata.get("location"):
        lines.append(f"- Location: {metadata['location']}")
    if metadata.get("camera_model"):
        lines.append(f"- Camera: {metadata['camera_model']}")
    if not any(metadata.get(k) for k in ("date", "location", "camera_model")):
        lines.append("- No metadata available")
    return "\n".join(lines)


_QUALITY_OK_WORDS = {"ok", "okay", "good", "fine", "clear", "normal", "none", "n/a", "na"}


def _quality_is_ok(raw: str) -> bool:
    """Return True if the quality response means 'no issues'."""
    raw = raw.strip().lower().rstrip(".")
    if raw in _QUALITY_OK_WORDS:
        return True
    if any(phrase in raw for phrase in (
        "no issue", "no flaw", "no visible", "no problem",
        "good quality", "looks good", "looks fine",
        "no defect", "well exposed", "properly exposed",
    )):
        return True
    return False


def _parse_response(raw: str) -> dict[str, Any]:
    """Parse a structured response with DESCRIPTION, FILENAME, TAGS, and QUALITY lines.

    Expected format:
        DESCRIPTION: A woman walking through a cobblestone alley
        FILENAME: woman-cobblestone-alley
        TAGS: woman, cobblestone, alley, europe, walking
        QUALITY: ok

    Falls back gracefully if the model doesn't follow the format exactly.
    Returns dict with keys: description, filename, tags, quality_flags.
    """
    # Pre-process: ensure each marker starts on its own line.
    # Handles models that return everything on one line, or omit newlines
    # between some markers (e.g. "description textFILENAME: name").
    processed = re.sub(r'(?i)(?<!\n)(DESCRIPTION:|FILENAME:|TAGS:|QUALITY:)', r'\n\1', raw).lstrip('\n')

    description = ""
    filename = ""
    tags: list[str] = []
    quality_flags: list[str] = []

    for line in processed.splitlines():
        line = line.strip()
        upper = line.upper()
        if upper.startswith("DESCRIPTION:"):
            description = line.split(":", 1)[1].strip()
        elif upper.startswith("FILENAME:"):
            filename = line.split(":", 1)[1].strip()
        elif upper.startswith("TAGS:"):
            tags_raw = line.split(":", 1)[1].strip()
            tags = [t.strip().lower() for t in tags_raw.split(",") if t.strip()]
        elif upper.startswith("QUALITY:"):
            quality_raw = line.split(":", 1)[1].strip().lower()
            if quality_raw and not _quality_is_ok(quality_raw):
                quality_flags = [q.strip() for q in quality_raw.split(",")
                                 if q.strip() and not _quality_is_ok(q.strip())]

    # Fallback: if parsing failed, use the whole response as description
    if not description and not filename:
        description = raw.strip()
        words = description.split()[:6]
        filename = "-".join(w.lower() for w in words)

    if not filename and description:
        words = description.split()[:6]
        filename = "-".join(w.lower() for w in words)

    return {"description": description, "filename": filename, "tags": tags, "quality_flags": quality_flags}
