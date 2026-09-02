from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetadataResult:
    date: str | None = None
    datetime_full: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    camera_model: str | None = None
    orientation: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    vision_description: str = ""
    final_filename: str = ""
    confidence_score: float = 0.0
    metadata: MetadataResult | None = None
    location_name: str | None = None
    ai_tags: list[str] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    error: str | None = None
