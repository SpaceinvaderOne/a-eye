from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import exifread
from PIL import Image, ExifTags

from backend.models import MetadataResult

logger = logging.getLogger(__name__)

# EXIF tag IDs we care about
_GPS_TAGS = {
    "GPS GPSLatitude",
    "GPS GPSLatitudeRef",
    "GPS GPSLongitude",
    "GPS GPSLongitudeRef",
}


def _rational_to_float(values: list) -> float:
    """Convert EXIF rational degrees/minutes/seconds to a decimal float."""
    d = float(values[0].num) / float(values[0].den)
    m = float(values[1].num) / float(values[1].den)
    s = float(values[2].num) / float(values[2].den)
    return d + m / 60.0 + s / 3600.0


def _extract_gps(tags: dict[str, Any]) -> tuple[float | None, float | None]:
    """Extract GPS lat/lon from exifread tags, or return (None, None)."""
    try:
        lat_tag = tags.get("GPS GPSLatitude")
        lat_ref = tags.get("GPS GPSLatitudeRef")
        lon_tag = tags.get("GPS GPSLongitude")
        lon_ref = tags.get("GPS GPSLongitudeRef")

        if not (lat_tag and lat_ref and lon_tag and lon_ref):
            return None, None

        lat = _rational_to_float(lat_tag.values)
        lon = _rational_to_float(lon_tag.values)

        if str(lat_ref) == "S":
            lat = -lat
        if str(lon_ref) == "W":
            lon = -lon

        return lat, lon
    except Exception:
        logger.debug("Failed to parse GPS data", exc_info=True)
        return None, None


def _extract_date(tags: dict[str, Any]) -> str | None:
    """Extract DateTimeOriginal, falling back to DateTime."""
    for tag_name in ("EXIF DateTimeOriginal", "EXIF DateTimeDigitized", "Image DateTime"):
        val = tags.get(tag_name)
        if val:
            # EXIF dates are "YYYY:MM:DD HH:MM:SS" — normalize to "YYYY-MM-DD"
            raw = str(val).strip()
            try:
                date_part = raw.split(" ")[0].replace(":", "-")
                # Reject sentinel/invalid dates
                if date_part.startswith("0000"):
                    continue
                parts = date_part.split("-")
                if len(parts) == 3 and "00" in parts[1:]:
                    continue
                return date_part
            except Exception:
                continue
    return None


def _extract_datetime(tags: dict[str, Any]) -> str | None:
    """Extract full ISO 8601 timestamp (YYYY-MM-DDTHH:MM:SS) when time is present."""
    for tag_name in ("EXIF DateTimeOriginal", "EXIF DateTimeDigitized", "Image DateTime"):
        val = tags.get(tag_name)
        if not val:
            continue
        raw = str(val).strip()
        parts = raw.split(" ", 1)
        date_part = parts[0].replace(":", "-")
        if date_part.startswith("0000"):
            continue
        dparts = date_part.split("-")
        if len(dparts) != 3 or "00" in dparts[1:]:
            continue
        tpart = parts[1].strip() if len(parts) == 2 else ""
        if len(tpart) == 8 and tpart[2] == ":" and tpart[5] == ":":
            return f"{date_part}T{tpart}"
        return date_part
    return None


def _extract_camera(tags: dict[str, Any]) -> str | None:
    """Extract camera make/model as a combined string."""
    make = str(tags.get("Image Make", "")).strip()
    model = str(tags.get("Image Model", "")).strip()
    if not make and not model:
        return None
    # Avoid duplication like "Apple Apple iPhone 15 Pro"
    if model.lower().startswith(make.lower()):
        return model
    return f"{make} {model}".strip()


def _get_orientation_pillow(file_path: Path) -> int | None:
    """Get EXIF orientation via Pillow as fallback."""
    try:
        with Image.open(file_path) as img:
            exif_data = img.getexif()
            orientation_tag = next(
                (k for k, v in ExifTags.TAGS.items() if v == "Orientation"), None
            )
            if orientation_tag and orientation_tag in exif_data:
                return exif_data[orientation_tag]
    except Exception:
        pass
    return None


def _serialize_tags(tags: dict[str, Any]) -> dict[str, str]:
    """Convert exifread tags to a JSON-serializable dict."""
    result = {}
    for key, val in tags.items():
        try:
            result[key] = str(val)
        except Exception:
            result[key] = repr(val)
    return result


async def extract_metadata(file_path: Path) -> MetadataResult:
    """Extract EXIF metadata from an image file.

    Uses exifread for tag parsing, Pillow as fallback for orientation.
    Returns a MetadataResult even if the image has no EXIF data.
    """
    tags: dict[str, Any] = {}

    try:
        with open(file_path, "rb") as f:
            tags = exifread.process_file(f, details=False)
    except Exception:
        logger.warning("exifread failed for %s", file_path, exc_info=True)

    date = _extract_date(tags)
    datetime_full = _extract_datetime(tags)
    gps_lat, gps_lon = _extract_gps(tags)
    camera_model = _extract_camera(tags)

    # Try exifread orientation first, fall back to Pillow
    orientation = None
    orient_tag = tags.get("Image Orientation")
    if orient_tag:
        try:
            orientation = int(str(orient_tag).split()[0])
        except (ValueError, IndexError):
            pass
    if orientation is None:
        orientation = _get_orientation_pillow(file_path)

    raw = _serialize_tags(tags)

    return MetadataResult(
        date=date,
        datetime_full=datetime_full,
        gps_lat=gps_lat,
        gps_lon=gps_lon,
        camera_model=camera_model,
        orientation=orientation,
        raw=raw,
    )
