from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from backend.database import count_images, get_image, list_images, update_image
from backend.filename import ensure_unique
from backend.preview import display_response, viewer_page
from backend.thumbnails import get_or_create_thumbnail
from backend.watcher import IMAGE_EXTENSIONS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspace")


def _get_workspace(request: Request):
    """Get the workspace instance, raising 503 if not ready."""
    ws = getattr(request.app.state, "workspace", None)
    if not ws or not ws.db:
        raise HTTPException(503, "Workspace not initialized")
    return ws


def _safe_path(base: Path, relative: str) -> Path:
    """Resolve a relative path against a base directory and verify it stays within bounds."""
    full = (base / relative).resolve()
    try:
        full.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(403, "Access denied")
    return full


class BatchIdsRequest(BaseModel):
    image_ids: list[int]
    context: str | None = None


# -- Stats ------------------------------------------------------------------

@router.get("/stats")
async def workspace_stats(request: Request):
    ws = _get_workspace(request)
    return await ws.get_stats()


# -- Upload -----------------------------------------------------------------

@router.post("/upload")
async def workspace_upload(
    request: Request,
    files: list[UploadFile] = File(...),
):
    """Upload files to the workspace and auto-start processing."""
    ws = _get_workspace(request)

    saved = 0
    skipped = 0

    for upload in files:
        if not upload.filename:
            skipped += 1
            continue

        # Sanitise: take only the basename (strip directory traversal)
        safe_name = Path(upload.filename).name.lstrip(".")
        if not safe_name:
            skipped += 1
            continue

        ext = Path(safe_name).suffix.lower()
        if ext not in IMAGE_EXTENSIONS:
            skipped += 1
            continue

        target = ensure_unique(ws.workspace_dir / safe_name)
        settings = request.app.state.settings
        max_bytes = settings.max_upload_size_mb * 1024 * 1024

        try:
            total_bytes = 0
            with open(target, "wb") as f:
                while True:
                    chunk = await upload.read(1024 * 64)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if max_bytes and total_bytes > max_bytes:
                        raise ValueError(f"File exceeds {settings.max_upload_size_mb}MB limit")
                    f.write(chunk)
            saved += 1
            logger.info("Workspace upload: %s → %s", upload.filename, target.name)
        except ValueError as exc:
            logger.warning("Workspace upload rejected %s: %s", upload.filename, exc)
            target.unlink(missing_ok=True)
            skipped += 1
        except OSError as exc:
            logger.error("Workspace upload failed for %s: %s", upload.filename, exc)
            target.unlink(missing_ok=True)
            skipped += 1

    # Scan and start processing
    if saved > 0:
        new_count = await ws.scan_workspace()
        logger.info("Workspace scan found %d new images", new_count)
        await ws.start_processing()

    return {"saved": saved, "skipped": skipped}


# -- Image listing ----------------------------------------------------------

@router.get("/images")
async def workspace_images(
    request: Request,
    status: str | None = None,
    offset: int = 0,
    limit: int = 50,
    sort: str = "created_at",
    sort_dir: str = "desc",
):
    ws = _get_workspace(request)
    images = await list_images(ws.db, status=status, offset=offset, limit=limit, sort=sort, sort_dir=sort_dir)
    total = await count_images(ws.db, status=status)
    return {"images": images, "total": total}


@router.get("/images/{image_id}")
async def workspace_image_detail(request: Request, image_id: int):
    ws = _get_workspace(request)
    image = await get_image(ws.db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    return image


# -- Thumbnails & files -----------------------------------------------------

@router.get("/images/{image_id}/thumbnail")
async def workspace_thumbnail(request: Request, image_id: int):
    ws = _get_workspace(request)
    settings = request.app.state.settings
    image = await get_image(ws.db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")

    source_path = _safe_path(ws.workspace_dir, image["file_path"])
    if not source_path.exists():
        raise HTTPException(404, "File not found")

    thumb = await get_or_create_thumbnail(
        image_id, source_path, ws.workspace_dir,
        max_size=settings.thumbnail_max_size,
        quality=settings.thumbnail_quality,
    )
    if not thumb:
        raise HTTPException(500, "Thumbnail generation failed")
    return FileResponse(thumb, media_type="image/jpeg")


@router.get("/images/{image_id}/file")
async def workspace_file(
    request: Request, image_id: int, download: bool = Query(False), display: bool = Query(False)
):
    ws = _get_workspace(request)
    image = await get_image(ws.db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")

    file_path = _safe_path(ws.workspace_dir, image["file_path"])
    if not file_path.exists():
        raise HTTPException(404, "File not found")

    if download:
        filename = image.get("current_filename") or image.get("original_filename") or file_path.name
        safe_fn = filename.replace('"', '').replace('\r', '').replace('\n', '')
        return FileResponse(
            file_path, filename=safe_fn,
            headers={"Content-Disposition": f'attachment; filename="{safe_fn}"'},
        )
    if display:
        return await display_response(file_path)
    return FileResponse(file_path)


@router.get("/images/{image_id}/viewer")
async def workspace_viewer(request: Request, image_id: int):
    """Full-size image viewer page for workspace images."""
    ws = _get_workspace(request)
    image = await get_image(ws.db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    return viewer_page(image, f"/api/workspace/images/{image_id}/file?display=true")


# -- Actions ----------------------------------------------------------------

@router.post("/images/{image_id}/approve")
async def workspace_approve(request: Request, image_id: int):
    ws = _get_workspace(request)
    try:
        result = await ws.approve_image(image_id)
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(500, str(exc))


@router.post("/images/approve-batch")
async def workspace_approve_batch(request: Request, body: BatchIdsRequest):
    ws = _get_workspace(request)
    results = []
    for image_id in body.image_ids:
        try:
            result = await ws.approve_image(image_id)
            results.append({"id": image_id, **result})
        except (ValueError, OSError) as exc:
            results.append({"id": image_id, "error": str(exc)})
    return {"results": results}


@router.post("/images/{image_id}/skip")
async def workspace_skip(request: Request, image_id: int):
    ws = _get_workspace(request)
    image = await get_image(ws.db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    await update_image(ws.db, image_id, status="skipped")
    return {"status": "skipped"}


@router.post("/images/{image_id}/process")
async def workspace_reprocess(request: Request, image_id: int):
    ws = _get_workspace(request)
    image = await get_image(ws.db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    # Parse optional context from JSON body
    context = None
    try:
        body = await request.json()
        context = body.get("context")
    except Exception:
        pass
    updates = {"status": "pending"}
    if context is not None:
        updates["processing_context"] = context.strip()[:500] if context.strip() else None
    await update_image(ws.db, image_id, **updates)
    await ws.start_processing()
    return {"status": "pending", "queued": True}


@router.post("/images/process-batch")
async def workspace_process_batch(request: Request, body: BatchIdsRequest):
    ws = _get_workspace(request)
    ctx = body.context.strip()[:500] if body.context and body.context.strip() else None
    count = 0
    for image_id in body.image_ids:
        image = await get_image(ws.db, image_id)
        if image:
            updates = {"status": "pending"}
            if body.context is not None:
                updates["processing_context"] = ctx
            await update_image(ws.db, image_id, **updates)
            count += 1
    if count > 0:
        await ws.start_processing()
    return {"queued": count}


# -- Delete -----------------------------------------------------------------

@router.delete("/images/{image_id}")
async def workspace_delete_image(request: Request, image_id: int):
    if not request.app.state.settings.destructive_mode_workspace:
        return JSONResponse(status_code=403, content={"error": "Destructive mode is not enabled for the workspace. Enable it in Settings."})
    ws = _get_workspace(request)
    try:
        result = await ws.delete_image(image_id)
        return result
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/images/delete-batch")
async def workspace_delete_batch(request: Request, body: BatchIdsRequest):
    if not request.app.state.settings.destructive_mode_workspace:
        return JSONResponse(status_code=403, content={"error": "Destructive mode is not enabled for the workspace. Enable it in Settings."})
    ws = _get_workspace(request)
    count = 0
    for image_id in body.image_ids:
        try:
            await ws.delete_image(image_id)
            count += 1
        except ValueError:
            pass
    return {"deleted": count}


# -- Download zip -----------------------------------------------------------

@router.post("/download")
async def workspace_download(request: Request):
    ws = _get_workspace(request)
    try:
        buffer = await ws.create_download_zip()
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    return StreamingResponse(
        iter([buffer.read()]),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="workspace_renamed.zip"'},
    )


@router.post("/images/download-batch")
async def workspace_download_batch(request: Request, body: BatchIdsRequest):
    """Generate a zip of selected workspace images."""
    ws = _get_workspace(request)

    images = []
    for image_id in body.image_ids:
        image = await get_image(ws.db, image_id)
        if image:
            images.append(image)

    if not images:
        raise HTTPException(404, "No valid images found")

    buffer = io.BytesIO()
    seen_names: set[str] = set()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        for image in images:
            try:
                file_path = _safe_path(ws.workspace_dir, image["file_path"])
            except HTTPException:
                continue
            if not file_path.exists():
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

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.read()]),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="workspace_download.zip"'},
    )


# -- Clear ------------------------------------------------------------------

@router.delete("/clear")
async def workspace_clear(request: Request):
    if not request.app.state.settings.destructive_mode_workspace:
        return JSONResponse(status_code=403, content={"error": "Destructive mode is not enabled for the workspace. Enable it in Settings."})
    ws = _get_workspace(request)
    result = await ws.clear()
    return result
