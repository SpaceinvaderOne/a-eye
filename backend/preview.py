"""Full-size image display for the viewer page.

Browser-native formats are served as the original file. Anything else
(HEIC, RAW, TIFF) is decoded and returned as an in-memory JPEG.
"""
from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

from backend.image_io import BROWSER_NATIVE_EXTENSIONS, render_preview_jpeg

logger = logging.getLogger(__name__)

PREVIEW_MAX_SIZE = 2048

# Cap simultaneous decodes so a burst of viewer requests can't swamp a small CPU.
_convert_slots = asyncio.Semaphore(2)


async def display_response(file_path: Path) -> Response:
    """Return the file as-is if browsers can show it, otherwise a converted JPEG."""
    if file_path.suffix.lower() in BROWSER_NATIVE_EXTENSIONS:
        return FileResponse(file_path)

    async with _convert_slots:
        try:
            data = await asyncio.to_thread(render_preview_jpeg, file_path, PREVIEW_MAX_SIZE)
        except Exception as exc:
            logger.warning("Cannot render %s for display: %s", file_path.name, exc)
            raise HTTPException(415, f"Cannot display {file_path.suffix} files in the browser")

    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


def viewer_page(image: dict, file_url: str) -> HTMLResponse:
    """Lightweight full-size viewer with close button and click-to-zoom."""
    filename = html.escape(image.get("current_filename") or image.get("original_filename") or "Image")
    page = f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{filename}</title>
<style>
body {{ margin:0; background:#000; display:flex; align-items:center; justify-content:center; min-height:100vh; }}
img {{ max-width:100%; max-height:100vh; object-fit:contain; cursor:zoom-in; }}
img.zoomed {{ max-width:none; max-height:none; cursor:zoom-out; }}
.close {{ position:fixed; top:1rem; right:1rem; background:rgba(0,0,0,0.7); color:#fff; border:1px solid #555;
  border-radius:4px; font-size:1.5rem; width:40px; height:40px; cursor:pointer;
  display:flex; align-items:center; justify-content:center; z-index:10; }}
.close:hover {{ background:rgba(255,255,255,0.2); }}
</style>
</head><body>
<button class="close" onclick="window.close()" title="Close">&times;</button>
<img src="{file_url}" alt="{filename}" onclick="this.classList.toggle('zoomed')">
<script>document.addEventListener('keydown',function(e){{ if(e.key==='Escape')window.close(); }});</script>
</body></html>"""
    return HTMLResponse(page)
