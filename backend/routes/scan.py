from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from backend.database import get_outcome_stats, get_pending_image_ids, get_stats, list_images, update_image

router = APIRouter()


@router.post("/scan")
async def trigger_scan(request: Request):
    """Trigger a directory scan for new images."""
    watcher = request.app.state.watcher

    if watcher.scan_in_progress:
        return {"status": "already_running", "message": "A scan is already in progress"}

    # Parse optional context from JSON body (backward compat: HTMX sends no body)
    context = None
    try:
        body = await request.json()
        context = body.get("context", "").strip()[:500] if body.get("context") else None
    except Exception:
        pass

    # Run scan in background so the API returns immediately
    async def _do_scan():
        new_count, skipped, new_ids = await watcher.scan_once()
        if context and new_ids:
            db = request.app.state.db
            for image_id in new_ids:
                await update_image(db, image_id, processing_context=context)
        request.app.state.last_scan_result = {
            "new": new_count,
            "skipped": skipped,
        }

    asyncio.create_task(_do_scan())

    return {"status": "started", "message": "Scan started" + (" with context" if context else "")}


@router.post("/scan/resume")
async def resume_processing(request: Request):
    """Clear the stop flag and re-enqueue any pending images."""
    worker = request.app.state.worker
    db = request.app.state.db

    worker.clear_stop()

    pending_ids = await get_pending_image_ids(db)
    if pending_ids:
        await worker.enqueue(pending_ids)

    return {"resumed": True, "enqueued": len(pending_ids)}


@router.post("/scan/stop")
async def stop_processing(request: Request):
    """Stop all active processing gracefully."""
    worker = request.app.state.worker
    watcher = request.app.state.watcher
    workspace = getattr(request.app.state, "workspace", None)

    # Abort scan discovery if running
    watcher.request_scan_stop()

    # Drain worker queue and stop picking up new images
    drained = worker.request_stop()

    # Stop workspace processing too
    if workspace and workspace.is_processing:
        workspace.request_stop()

    return {"stopped": True, "drained": drained}


@router.get("/scan/status")
async def scan_status(request: Request):
    """Current scan progress."""
    watcher = request.app.state.watcher
    last_result = getattr(request.app.state, "last_scan_result", None)

    return {
        "scanning": watcher.scan_in_progress,
        "last_result": last_result,
        "worker": {
            "running": request.app.state.worker.is_running,
            "pending": request.app.state.worker.pending_count,
            "processed": request.app.state.worker.processed_count,
            "errors": request.app.state.worker.error_count,
        },
    }


@router.get("/dashboard/status")
async def dashboard_status(request: Request):
    """Combined stats + worker + progress for the dashboard."""
    db = request.app.state.db
    stats = await get_stats(db)
    outcomes = await get_outcome_stats(db)
    watcher = request.app.state.watcher
    worker = request.app.state.worker

    total = stats.get("total", 0)
    done = sum(stats.get(s, 0) for s in ("proposed", "renamed", "completed", "error", "skipped"))
    in_flight = stats.get("processing", 0)

    # Currently-processing images (for live thumbnail display)
    processing_images = await list_images(db, status="processing", limit=10)
    active = [{"id": img["id"], "filename": img["original_filename"]} for img in processing_images]

    # Schedule status
    scheduler = getattr(request.app.state, "scheduler", None)
    schedule_status = scheduler.get_status() if scheduler else {"enabled": False}

    return {
        "stats": stats,
        "outcomes": outcomes,
        "worker": {
            "running": worker.is_running,
            "paused": worker.is_paused,
            "pending": worker.pending_count,
            "processed": worker.processed_count,
            "errors": worker.error_count,
            "stop_requested": worker.stop_requested,
        },
        "scanning": watcher.scan_in_progress,
        "schedule": schedule_status,
        "progress": {
            "total": total,
            "completed": done,
            "in_progress": in_flight,
            "percentage": round((done / total) * 100, 1) if total > 0 else 0,
        },
        "active_images": active,
    }
