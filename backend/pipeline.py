from __future__ import annotations

import logging
from pathlib import Path

from backend.config import Settings
from backend.confidence import score_confidence
from backend.filename import render_template, sanitize_filename
from backend.geocode import reverse_geocode_location
from backend.metadata import extract_metadata
from backend.models import MetadataResult, PipelineResult
from backend.ollama_client import OllamaClient

logger = logging.getLogger(__name__)


async def process_image(
    file_path: Path,
    settings: Settings,
    ollama: OllamaClient,
    processing_context: str | None = None,
) -> PipelineResult:
    """Run the full rename pipeline on a single image.

    Stage 1: Extract EXIF metadata + reverse geocode GPS
    Stage 2: Vision model — describe, name, tag, and assess quality

    Returns a PipelineResult with all intermediate data.
    """
    result = PipelineResult()

    # ── Stage 1: Metadata ──────────────────────────────────────────────────

    try:
        metadata = await extract_metadata(file_path)
        result.metadata = metadata
    except Exception as exc:
        logger.error("Metadata extraction failed for %s: %s", file_path, exc)
        metadata = MetadataResult()
        result.metadata = metadata

    location_name = None
    if (
        settings.use_gps
        and metadata.gps_lat is not None
        and metadata.gps_lon is not None
    ):
        location_name = reverse_geocode_location(
            metadata.gps_lat, metadata.gps_lon, settings.gps_detail
        )
    result.location_name = location_name

    # Build a metadata dict for prompts and confidence scoring
    meta_for_prompt = {
        "date": metadata.date if settings.use_exif_date else None,
        "location": location_name,
        "camera_model": metadata.camera_model,
        "gps_lat": metadata.gps_lat,
        "gps_lon": metadata.gps_lon,
    }

    # ── Stage 2: Vision ──────────────────────────────────────────────────

    try:
        description, suggested_name, tags, quality_flags = await ollama.describe_and_name_image(
            image_path=file_path,
            metadata=meta_for_prompt,
            include_tags=True,  # Always generate tags for the database
            processing_context=processing_context,
        )
        result.vision_description = description
        result.ai_tags = tags
        result.quality_flags = quality_flags
        raw_name = suggested_name

    except Exception as exc:
        # Many httpx / library exceptions have an empty str(exc) but a useful
        # repr(). Log both type and repr with traceback so failures are
        # diagnosable from the log alone.
        logger.error(
            "AI processing failed for %s: %s: %r",
            file_path, type(exc).__name__, exc,
            exc_info=True,
        )
        result.error = str(exc) or f"{type(exc).__name__}: {exc!r}"
        return result

    # ── Filename assembly (skip when rename disabled) ──────────────────────

    if settings.process_rename and raw_name:
        # Sanitize the AI-suggested name
        sanitized_description = sanitize_filename(
            raw_name, max_len=80, case=settings.filename_case
        )

        # Sanitize location for use in filenames
        sanitized_location = None
        if location_name:
            sanitized_location = sanitize_filename(
                location_name, max_len=30, case=settings.filename_case
            )

        # Render the template
        final_name = render_template(
            template=settings.filename_template,
            date=metadata.date if settings.use_exif_date else None,
            location=sanitized_location,
            description=sanitized_description,
            camera=None,  # Camera is rarely useful in filenames
        )

        # Final sanitization pass on the assembled name
        result.final_filename = sanitize_filename(
            final_name, max_len=settings.max_filename_len, case=settings.filename_case
        )

        # ── Confidence scoring ─────────────────────────────────────────────
        result.confidence_score = score_confidence(
            vision_description=result.vision_description,
            proposed_filename=result.final_filename,
            metadata=meta_for_prompt,
        )
    else:
        result.final_filename = ""
        result.confidence_score = 0.0

    return result
