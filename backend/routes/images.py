from __future__ import annotations

import asyncio
import csv
import html
import io
import json
import logging
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from backend.database import (
    count_images,
    delete_all_history,
    delete_reverted_history,
    get_image,
    get_rename_history,
    insert_rename_history,
    list_images,
    mark_image_history_reverted,
    mark_rename_reverted,
    update_image,
)
from backend.filename import ensure_unique, sanitize_filename
from backend.thumbnails import get_or_create_thumbnail
from backend.xmp_writer import rename_xmp_sidecar

logger = logging.getLogger(__name__)

router = APIRouter()


# -- Security helpers --------------------------------------------------------

def _safe_path(base: Path, relative: str) -> Path:
    """Resolve a relative path against a base directory and verify it stays within bounds."""
    full = (base / relative).resolve()
    try:
        full.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(403, "Access denied")
    return full


def _require_writable(request: Request) -> None:
    """Raise 403 if photos directory is read-only."""
    if getattr(request.app.state, "photos_readonly", False):
        raise HTTPException(403, "Photos directory is read-only")


# -- Request models ----------------------------------------------------------

class ApproveRequest(BaseModel):
    filename: str | None = None  # Override the proposed name


class RenameRequest(BaseModel):
    filename: str  # Custom filename (no extension)


class BatchApproveRequest(BaseModel):
    image_ids: list[int]


class BatchProcessRequest(BaseModel):
    image_ids: list[int]
    context: str | None = None


class BatchDownloadRequest(BaseModel):
    image_ids: list[int]


# -- List / Detail -----------------------------------------------------------

@router.get("/images")
async def api_list_images(
    request: Request,
    status: str | None = None,
    folder: str | None = None,
    sort: str = "created_at",
    sort_dir: str = "desc",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = request.app.state.db
    images = await list_images(db, status=status, folder=folder, sort=sort, sort_dir=sort_dir, offset=offset, limit=limit)
    total = await count_images(db, status=status)
    return {"images": images, "total": total, "offset": offset, "limit": limit}


@router.get("/images/random")
async def api_random_images(
    request: Request,
    count: int = Query(2, ge=1, le=50),
    tag: str | None = None,
    exclude: str | None = None,
):
    """Return random processed images for the dashboard showcase."""
    db = request.app.state.db
    conditions = ["status IN ('renamed', 'proposed', 'approved', 'skipped', 'completed')"]
    params: dict[str, Any] = {"limit": count}
    if tag:
        tags = [t.strip().lower() for t in tag.split(",") if t.strip()]
        if tags:
            tag_clauses = []
            for i, t in enumerate(tags):
                key = f"tag{i}"
                tag_clauses.append(
                    f"(LOWER(COALESCE(ai_tags,'')) LIKE :{key}"
                    f" OR ' ' || LOWER(COALESCE(vision_description,'')) || ' ' LIKE :{key}"
                    f" OR REPLACE(REPLACE(' ' || LOWER(COALESCE(current_filename,'')) || ' ', '_', ' '), '-', ' ') LIKE :{key})"
                )
                params[key] = f"%{t}%"
            conditions.append("(" + " OR ".join(tag_clauses) + ")")

    # Parse IDs to exclude (currently-displayed photos)
    exclude_ids = []
    if exclude:
        exclude_ids = [int(x) for x in exclude.split(",") if x.strip().isdigit()]

    if exclude_ids:
        placeholders = ",".join(f":ex{i}" for i in range(len(exclude_ids)))
        for i, eid in enumerate(exclude_ids):
            params[f"ex{i}"] = eid
        where = " AND ".join(conditions + [f"id NOT IN ({placeholders})"])
        query = f"SELECT * FROM images WHERE {where} ORDER BY RANDOM() LIMIT :limit"
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        # Fallback: if exclusion left no results, retry without it
        if not rows:
            fallback_params = {k: v for k, v in params.items() if not k.startswith("ex")}
            where = " AND ".join(conditions)
            query = f"SELECT * FROM images WHERE {where} ORDER BY RANDOM() LIMIT :limit"
            cursor = await db.execute(query, fallback_params)
            rows = await cursor.fetchall()
    else:
        where = " AND ".join(conditions)
        query = f"SELECT * FROM images WHERE {where} ORDER BY RANDOM() LIMIT :limit"
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

    columns = [d[0] for d in cursor.description]
    images = [dict(zip(columns, row)) for row in rows]
    return {"images": images}


@router.get("/images/{image_id}")
async def api_get_image(request: Request, image_id: int):
    db = request.app.state.db
    image = await get_image(db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    return image


# -- Thumbnails --------------------------------------------------------------

@router.get("/images/{image_id}/thumbnail")
async def api_get_thumbnail(request: Request, image_id: int):
    db = request.app.state.db
    settings = request.app.state.settings
    image = await get_image(db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")

    photos_dir = Path(settings.photos_dir)
    source_path = _safe_path(photos_dir, image["file_path"])
    if not source_path.exists():
        raise HTTPException(404, "Source image not found")

    thumb = await get_or_create_thumbnail(
        image_id, source_path, Path(settings.data_dir),
        max_size=settings.thumbnail_max_size,
        quality=settings.thumbnail_quality,
    )
    if not thumb:
        raise HTTPException(500, "Failed to generate thumbnail")

    return FileResponse(thumb, media_type="image/jpeg")


@router.get("/images/{image_id}/file")
async def api_get_file(request: Request, image_id: int, download: bool = Query(False)):
    """Serve the original full-size image file."""
    db = request.app.state.db
    settings = request.app.state.settings
    image = await get_image(db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")

    photos_dir = Path(settings.photos_dir)
    file_path = _safe_path(photos_dir, image["file_path"])
    if not file_path.exists():
        raise HTTPException(404, "File not found on disk")

    if download:
        filename = image.get("current_filename") or image.get("original_filename") or file_path.name
        safe_fn = filename.replace('"', '').replace('\r', '').replace('\n', '')
        return FileResponse(
            file_path,
            filename=safe_fn,
            headers={"Content-Disposition": f'attachment; filename="{safe_fn}"'},
        )
    return FileResponse(file_path)


@router.get("/images/{image_id}/viewer")
async def api_image_viewer(request: Request, image_id: int):
    """Lightweight full-size image viewer with close button."""
    db = request.app.state.db
    image = await get_image(db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")

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
<img src="/api/images/{image_id}/file" alt="{filename}" onclick="this.classList.toggle('zoomed')">
<script>document.addEventListener('keydown',function(e){{ if(e.key==='Escape')window.close(); }});</script>
</body></html>"""
    return HTMLResponse(page)


# -- Processing --------------------------------------------------------------

@router.post("/images/{image_id}/process")
async def api_process_image(request: Request, image_id: int):
    """(Re)process a single image through the pipeline."""
    db = request.app.state.db
    image = await get_image(db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")

    # Parse optional context from JSON body (backward compat: no body = no context)
    context = None
    try:
        body = await request.json()
        context = body.get("context")
    except Exception:
        pass
    updates: dict = {"status": "pending", "error_message": None}
    if context is not None:
        updates["processing_context"] = context.strip()[:500] if context.strip() else None
    await update_image(db, image_id, **updates)

    worker = request.app.state.worker
    await worker.enqueue([image_id])
    return {"status": "enqueued", "image_id": image_id}


@router.post("/images/process-batch")
async def api_process_batch(request: Request, body: BatchProcessRequest):
    """Process multiple images through the pipeline."""
    db = request.app.state.db
    ctx = body.context.strip()[:500] if body.context and body.context.strip() else None
    for image_id in body.image_ids:
        updates: dict = {"status": "pending", "error_message": None}
        if body.context is not None:
            updates["processing_context"] = ctx
        await update_image(db, image_id, **updates)
    worker = request.app.state.worker
    count = await worker.enqueue(body.image_ids)
    return {"status": "enqueued", "count": count}


@router.post("/images/retry-all-errors")
async def api_retry_all_errors(request: Request):
    """Reset all error images to pending and enqueue them for reprocessing."""
    db = request.app.state.db
    cursor = await db.execute("SELECT id FROM images WHERE status = 'error'")
    error_ids = [row[0] for row in await cursor.fetchall()]
    if error_ids:
        await db.execute(
            "UPDATE images SET status = 'pending', error_message = NULL, retry_count = 0 "
            "WHERE status = 'error'"
        )
        await db.commit()
    worker = request.app.state.worker
    count = await worker.enqueue(error_ids)
    return {"status": "enqueued", "count": count}


@router.post("/images/download-batch")
async def api_download_batch(request: Request, body: BatchDownloadRequest):
    """Generate a zip file containing selected images and stream it."""
    db = request.app.state.db
    settings = request.app.state.settings
    photos_dir = Path(settings.photos_dir)

    images = []
    for image_id in body.image_ids:
        image = await get_image(db, image_id)
        if image:
            images.append(image)

    if not images:
        raise HTTPException(404, "No valid images found")

    buffer = io.BytesIO()
    seen_names: set[str] = set()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        for image in images:
            file_path = (photos_dir / image["file_path"]).resolve()
            if not file_path.exists():
                continue
            try:
                file_path.relative_to(photos_dir.resolve())
            except ValueError:
                continue

            arcname = image.get("current_filename") or image.get("original_filename") or file_path.name
            if arcname in seen_names:
                stem = Path(arcname).stem
                ext = Path(arcname).suffix
                counter = 1
                while f"{stem}_{counter}{ext}" in seen_names:
                    counter += 1
                arcname = f"{stem}_{counter}{ext}"
            seen_names.add(arcname)
            zf.write(file_path, arcname)
            sidecar = file_path.parent / f"{file_path.name}.xmp"
            if sidecar.exists():
                zf.write(sidecar, f"{arcname}.xmp")

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.read()]),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="a-eye_download.zip"'},
    )


# -- Approve / Skip / Rename ------------------------------------------------

@router.post("/images/{image_id}/approve")
async def api_approve_image(request: Request, image_id: int, body: ApproveRequest | None = None):
    """Approve the proposed filename (optionally override it)."""
    _require_writable(request)
    db = request.app.state.db
    settings = request.app.state.settings
    image = await get_image(db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")

    if image["status"] not in ("proposed", "error"):
        raise HTTPException(400, f"Image is in '{image['status']}' state, cannot approve")

    final_name = body.filename if body and body.filename else image.get("final_filename")
    if not final_name:
        raise HTTPException(400, "No filename to approve")

    # Sanitize user-provided filenames
    if body and body.filename:
        final_name = sanitize_filename(final_name, max_len=settings.max_filename_len, case=settings.filename_case)

    result = await _do_rename(db, settings, image_id, image, final_name)
    return result


@router.post("/images/{image_id}/skip")
async def api_skip_image(request: Request, image_id: int):
    db = request.app.state.db
    image = await get_image(db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")

    await update_image(db, image_id, status="skipped", quality_flags=[])
    return {"status": "skipped", "image_id": image_id}


@router.post("/images/{image_id}/unskip")
async def api_unskip_image(request: Request, image_id: int):
    db = request.app.state.db
    image = await get_image(db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")

    if image["status"] != "skipped":
        raise HTTPException(400, "Image is not skipped")

    if image.get("final_filename"):
        # Already has a proposed name — return to proposed state (no reprocessing)
        await update_image(db, image_id, status="proposed")
        return {"status": "unskipped", "image_id": image_id, "reprocessed": False}
    else:
        # No proposed name yet — needs processing
        await update_image(db, image_id, status="pending")
        worker = request.app.state.worker
        await worker.enqueue([image_id])
        return {"status": "unskipped", "image_id": image_id, "reprocessed": True}


@router.post("/images/{image_id}/rename")
async def api_rename_image(request: Request, image_id: int, body: RenameRequest):
    """Force rename with a custom filename."""
    _require_writable(request)
    db = request.app.state.db
    settings = request.app.state.settings
    image = await get_image(db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")

    # Always sanitize user-provided filenames
    safe_name = sanitize_filename(body.filename, max_len=settings.max_filename_len, case=settings.filename_case)
    result = await _do_rename(db, settings, image_id, image, safe_name)
    return result


@router.post("/images/approve-batch")
async def api_approve_batch(request: Request, body: BatchApproveRequest):
    """Bulk approve proposed filenames."""
    _require_writable(request)
    db = request.app.state.db
    settings = request.app.state.settings
    results = []

    for image_id in body.image_ids:
        image = await get_image(db, image_id)
        if not image or image["status"] != "proposed" or not image.get("final_filename"):
            results.append({"image_id": image_id, "status": "skipped", "reason": "not eligible"})
            continue

        try:
            result = await _do_rename(db, settings, image_id, image, image["final_filename"])
            results.append(result)
        except Exception:
            logger.error("Batch approve failed for image %d", image_id, exc_info=True)
            results.append({"image_id": image_id, "status": "error", "error": "Rename failed"})

    return {"results": results}


# -- Revert ------------------------------------------------------------------

@router.post("/images/{image_id}/revert")
async def api_revert_image(request: Request, image_id: int):
    """Undo the most recent rename for this image."""
    _require_writable(request)
    db = request.app.state.db
    settings = request.app.state.settings

    image = await get_image(db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")

    if image["status"] not in ("renamed", "proposed"):
        raise HTTPException(400, "Image has not been renamed")

    # Find the most recent non-reverted rename
    history = await get_rename_history(db, image_id=image_id, limit=1)
    if not history:
        raise HTTPException(400, "No rename history found")

    entry = history[0]
    if entry.get("reverted_at"):
        raise HTTPException(400, "Already reverted")

    photos_dir = Path(settings.photos_dir)
    current_path = _safe_path(photos_dir, image["file_path"])
    revert_path = _safe_path(photos_dir, entry["old_path"])

    # Make sure we don't overwrite something at the old path
    revert_path = ensure_unique(revert_path)

    try:
        os.rename(current_path, revert_path)
        rename_xmp_sidecar(current_path, revert_path)
    except FileNotFoundError:
        raise HTTPException(400, "File not found on disk")
    except OSError:
        logger.error("Revert failed for image %d", image_id, exc_info=True)
        raise HTTPException(500, "Revert failed")

    new_relative = str(revert_path.relative_to(photos_dir.resolve()))
    await mark_rename_reverted(db, entry["id"])
    await insert_rename_history(db, image_id, old_path=image["file_path"], new_path=new_relative)
    await update_image(
        db, image_id,
        file_path=new_relative,
        current_filename=revert_path.name,
        status="proposed",
        renamed_at=None,
    )

    return {
        "status": "reverted",
        "image_id": image_id,
        "old_name": current_path.name,
        "restored_name": revert_path.name,
    }


# -- History -----------------------------------------------------------------

@router.get("/history")
async def api_get_history(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = request.app.state.db
    history = await get_rename_history(db, offset=offset, limit=limit)

    # Enrich with image info
    for entry in history:
        image = await get_image(db, entry["image_id"])
        entry["image"] = image

    return {"history": history, "offset": offset, "limit": limit}


@router.get("/history/export")
async def api_export_history(
    request: Request,
    format: str = Query("csv", pattern="^(csv|json)$"),
):
    """Export rename history as CSV or JSON."""
    db = request.app.state.db
    history = await get_rename_history(db, offset=0, limit=10000)

    if format == "json":
        return StreamingResponse(
            io.BytesIO(json.dumps(history, indent=2).encode()),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=rename_history.json"},
        )

    # CSV
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["id", "image_id", "old_path", "new_path", "renamed_at", "reverted_at"])
    writer.writeheader()
    writer.writerows(history)

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=rename_history.csv"},
    )


@router.delete("/history/reverted")
async def api_clear_reverted_history(request: Request):
    """Delete all reverted (dimmed) history entries."""
    db = request.app.state.db
    count = await delete_reverted_history(db)
    return {"deleted": count}


@router.delete("/history/all")
async def api_clear_all_history(request: Request):
    """Delete all history entries (removes undo capability)."""
    db = request.app.state.db
    count = await delete_all_history(db)
    return {"deleted": count}


# -- Internal helpers --------------------------------------------------------

async def _do_rename(
    db: Any, settings: Any, image_id: int, image: dict, new_name: str
) -> dict:
    """Rename a file on disk and update the DB."""
    photos_dir = Path(settings.photos_dir)
    current_path = _safe_path(photos_dir, image["file_path"])

    extension = current_path.suffix
    new_path = current_path.parent / f"{new_name}{extension}"
    new_path = ensure_unique(new_path)

    # Validate the final path is still within photos_dir
    resolved_new = new_path.resolve()
    try:
        resolved_new.relative_to(photos_dir.resolve())
    except ValueError:
        raise HTTPException(403, "Access denied")

    if settings.dry_run:
        await update_image(
            db, image_id,
            status="renamed",
            current_filename=new_path.name,
            renamed_at=datetime.now(timezone.utc).isoformat(),
            quality_flags=[],
        )
        return {"status": "renamed", "image_id": image_id, "new_name": new_path.name, "dry_run": True}

    try:
        os.rename(current_path, new_path)
        rename_xmp_sidecar(current_path, new_path)
    except FileNotFoundError:
        await update_image(db, image_id, status="error", error_message="File not found on disk")
        raise HTTPException(400, "File not found on disk")
    except OSError:
        logger.error("Rename failed for image %d", image_id, exc_info=True)
        await update_image(db, image_id, status="error", error_message="Rename failed")
        raise HTTPException(500, "Rename failed")

    new_relative = str(new_path.relative_to(photos_dir.resolve()))
    await mark_image_history_reverted(db, image_id)
    await insert_rename_history(db, image_id, old_path=image["file_path"], new_path=new_relative)
    await update_image(
        db, image_id,
        status="renamed",
        file_path=new_relative,
        current_filename=new_path.name,
        renamed_at=datetime.now(timezone.utc).isoformat(),
        quality_flags=[],
    )

    return {"status": "renamed", "image_id": image_id, "new_name": new_path.name}
