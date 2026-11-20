from __future__ import annotations

import logging

import reverse_geocode

logger = logging.getLogger(__name__)


def reverse_geocode_location(
    lat: float, lon: float, detail: str = "city"
) -> str | None:
    """Reverse-geocode GPS coordinates to a place name (offline).

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        detail: Level of detail — "city", "city-country", "full", or "coordinates".

    Returns:
        A location string, or None on failure.
    """
    if detail == "coordinates":
        return f"{lat:.4f}_{lon:.4f}"

    try:
        results = reverse_geocode.search([(lat, lon)])
        if not results:
            return None

        info = results[0]
        city = info.get("city", "")
        country = info.get("country", "")

        if detail == "city":
            return city or None
        elif detail == "city-country":
            parts = [p for p in (city, country) if p]
            return ", ".join(parts) or None
        elif detail == "full":
            state = info.get("state", "")
            parts = [p for p in (city, state, country) if p]
            return ", ".join(parts) or None
        else:
            return city or None
    except Exception:
        logger.warning("Reverse geocoding failed for (%s, %s)", lat, lon, exc_info=True)
        return None
