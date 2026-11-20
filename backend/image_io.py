"""Shared image-opening helper with RAW format support.

Tries Pillow first (handles JPEG, PNG, TIFF, BMP, WebP, HEIC, AVIF, DNG).
Falls back to rawpy for camera RAW formats (CR2, NEF, ARW, ORF, RW2, etc.).
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

import rawpy
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Formats every mainstream browser can display without conversion.
BROWSER_NATIVE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

_RAW_EXTENSIONS = {
    ".raw", ".cr2", ".nef", ".arw", ".dng", ".orf", ".rw2",
    ".pef", ".srw", ".raf", ".cr3", ".3fr", ".kdc", ".mrw",
}


def open_image(path: Path) -> Image.Image:
    """Open an image file, with automatic RAW fallback.

    Returns a PIL Image in RGB mode with EXIF orientation applied.
    The caller is responsible for closing the image when done.
    """
    try:
        img = Image.open(path)
        img.load()  # Force read so errors surface here
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        return img
    except Exception:
        # If Pillow can't open it, try rawpy for RAW formats
        if path.suffix.lower() not in _RAW_EXTENSIONS:
            raise

    logger.debug("Pillow can't open %s, trying rawpy", path.name)
    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=False)
    img = Image.fromarray(rgb)
    return img


def render_preview_jpeg(path: Path, max_size: int = 2048, quality: int = 85) -> bytes:
    """Decode an image and return it as JPEG bytes scaled to fit within max_size.

    Used to display formats browsers can't decode themselves (HEIC, RAW, TIFF).
    Everything happens in memory — nothing is written to disk.
    """
    img = open_image(path)
    try:
        img.thumbnail((max_size, max_size), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()
    finally:
        img.close()
