from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.database import (
    get_image,
    insert_rename_history,
    update_image,
)

logger = logging.getLogger(__name__)

router = APIRouter()

TRASH_DIR_NAME = ".trash"


class TrashBatchRequest(BaseModel):
    ids: list[int]


# -- Helpers -----------------------------------------------------------------

def _trash_dir(photos_dir: Path) -> Path:
    """Return the .trash directory path, creating it on first use."""
    trash = photos_dir / TRASH_DIR_NAME
    trash.mkdir(parents=True, exist_ok=True)
    return trash


def _safe_path(base: Path, relative: str) -> Path:
    """Resolve a relative path against a base directory and verify it stays within bounds."""
    full = (base / relative).resolve()
    try:
        full.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(403, "Access denied")
    return full


def _trash_destination(photos_dir: Path, relative_path: str) -> Path:
    """Compute the .trash destination mirroring the original relative path."""
    trash = _trash_dir(photos_dir)
    dest = trash / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def _move_with_sidecar(src: Path, dst: Path) -> None:
    """Move a file and its XMP sidecar (if present)."""
    os.rename(src, dst)
    # Move XMP sidecar if it exists
    xmp_src = src.with_suffix(src.suffix + ".xmp")
    if xmp_src.exists():
        xmp_dst = dst.with_suffix(dst.suffix + ".xmp")
        xmp_dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(xmp_src, xmp_dst)


# -- Trash a single image ---------------------------------------------------

async def _trash_one(request: Request, image_id: int) -> dict:
    """Move a single image to .trash and update the DB. Returns result dict."""
    db = request.app.state.db
    settings = request.app.state.settings
    photos_dir = Path(settings.photos_dir)

    image = await get_image(db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    if image["status"] == "trashed":
        raise HTTPException(400, "Image is already trashed")

    current_path = _safe_path(photos_dir, image["file_path"])
    if not current_path.exists():
        # File missing on disk — just mark as trashed in DB
        await update_image(db, image_id, status="trashed")
        return {"image_id": image_id, "status": "trashed", "note": "file missing on disk"}

    # Compute destination inside .trash
    trash_dest = _trash_destination(photos_dir, image["file_path"])

    try:
        _move_with_sidecar(current_path, trash_dest)
    except OSError as exc:
        logger.error("Failed to trash image %d: %s", image_id, exc)
        raise HTTPException(500, f"Failed to move file to trash: {exc}")

    # Record the move in rename_history (for undo)
    trash_relative = str(trash_dest.relative_to(photos_dir.resolve()))
    await insert_rename_history(db, image_id, old_path=image["file_path"], new_path=trash_relative)

    # Update image record
    await update_image(
        db, image_id,
        status="trashed",
        file_path=trash_relative,
    )

    logger.info("Trashed image %d: %s → .trash", image_id, image["file_path"])
    return {"image_id": image_id, "status": "trashed"}


# -- Endpoints ---------------------------------------------------------------

@router.post("/images/{image_id}/trash")
async def api_trash_image(request: Request, image_id: int):
    """Move a single image to .trash."""
    if getattr(request.app.state, "photos_readonly", False):
        return JSONResponse(status_code=403, content={"error": "Photos directory is read-only"})
    if not request.app.state.settings.destructive_mode_library:
        return JSONResponse(status_code=403, content={"error": "Destructive mode is not enabled for the library. Enable it in Settings."})
    return await _trash_one(request, image_id)


@router.post("/images/trash-batch")
async def api_trash_batch(request: Request, body: TrashBatchRequest):
    """Move multiple images to .trash."""
    if getattr(request.app.state, "photos_readonly", False):
        return JSONResponse(status_code=403, content={"error": "Photos directory is read-only"})
    if not request.app.state.settings.destructive_mode_library:
        return JSONResponse(status_code=403, content={"error": "Destructive mode is not enabled for the library. Enable it in Settings."})
    results = []
    for image_id in body.ids:
        try:
            result = await _trash_one(request, image_id)
            results.append(result)
        except HTTPException as exc:
            results.append({"image_id": image_id, "status": "error", "error": exc.detail})
        except Exception as exc:
            logger.error("Failed to trash image %d", image_id, exc_info=True)
            results.append({"image_id": image_id, "status": "error", "error": str(exc)})

    trashed_count = sum(1 for r in results if r.get("status") == "trashed")
    return {"results": results, "trashed": trashed_count}


@router.post("/images/{image_id}/restore")
async def api_restore_image(request: Request, image_id: int):
    """Restore a single image from .trash to its original location."""
    if getattr(request.app.state, "photos_readonly", False):
        return JSONResponse(status_code=403, content={"error": "Photos directory is read-only"})

    db = request.app.state.db
    settings = request.app.state.settings
    photos_dir = Path(settings.photos_dir)

    image = await get_image(db, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    if image["status"] != "trashed":
        raise HTTPException(400, "Image is not trashed")

    # Get the rename_history entry to find the original path
    from backend.database import get_rename_history, mark_rename_reverted
    history = await get_rename_history(db, image_id=image_id, limit=1)
    if not history:
        raise HTTPException(400, "No history found — cannot determine original location")

    entry = history[0]
    original_relative = entry["old_path"]

    # Current location (in .trash)
    trash_path = _safe_path(photos_dir, image["file_path"])

    # Restore destination
    restore_path = _safe_path(photos_dir, original_relative)
    restore_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure unique filename if something else is now at the original path
    from backend.filename import ensure_unique
    restore_path = ensure_unique(restore_path)

    if trash_path.exists():
        try:
            _move_with_sidecar(trash_path, restore_path)
        except OSError as exc:
            logger.error("Failed to restore image %d: %s", image_id, exc)
            raise HTTPException(500, f"Failed to restore file: {exc}")
    else:
        logger.warning("Trash file not found for image %d, updating DB only", image_id)

    # Mark history entry as reverted
    await mark_rename_reverted(db, entry["id"])

    # Update image record back to proposed
    restore_relative = str(restore_path.relative_to(photos_dir.resolve()))
    await update_image(
        db, image_id,
        status="proposed",
        file_path=restore_relative,
    )

    logger.info("Restored image %d: .trash → %s", image_id, restore_relative)
    return {"image_id": image_id, "status": "restored", "restored_path": restore_relative}


@router.delete("/trash")
async def api_empty_trash(request: Request):
    """Permanently delete all trashed files and remove DB records."""
    if not request.app.state.settings.destructive_mode_library:
        return JSONResponse(status_code=403, content={"error": "Destructive mode is not enabled for the library. Enable it in Settings."})
    db = request.app.state.db
    settings = request.app.state.settings
    photos_dir = Path(settings.photos_dir)
    trash = photos_dir / TRASH_DIR_NAME

    # Count trashed images
    from backend.database import list_images, count_images
    trashed_count = await count_images(db, status="trashed")

    if trashed_count == 0:
        return {"deleted": 0}

    # Delete all trashed image records, their thumbnails, and their history
    from backend.thumbnails import delete_thumbnail
    data_dir = Path(settings.data_dir)
    trashed_images = await list_images(db, status="trashed", limit=10000)
    for img in trashed_images:
        # Delete cached thumbnail
        await delete_thumbnail(img["id"], data_dir)
        # Delete rename_history entries for this image
        await db.execute("DELETE FROM rename_history WHERE image_id = ?", (img["id"],))
        # Delete the image record
        await db.execute("DELETE FROM images WHERE id = ?", (img["id"],))
    await db.commit()

    # Remove the .trash directory contents
    if trash.exists():
        shutil.rmtree(trash)
        logger.info("Emptied trash directory: %s", trash)

    logger.info("Permanently deleted %d trashed images", trashed_count)
    return {"deleted": trashed_count}


@router.get("/trash/stats")
async def api_trash_stats(request: Request):
    """Return trash file count and total size."""
    db = request.app.state.db
    cursor = await db.execute(
        "SELECT COUNT(*), COALESCE(SUM(file_size), 0) FROM images WHERE status = 'trashed'"
    )
    row = await cursor.fetchone()
    count = row[0]
    total_size = row[1]

    return {
        "count": count,
        "total_size": total_size,
        "total_size_human": _human_size(total_size),
    }


def _human_size(size_bytes: int) -> str:
    """Convert bytes to a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
