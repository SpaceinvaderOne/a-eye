from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse

from backend.watcher import IMAGE_EXTENSIONS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/database")

SQLITE_MAGIC = b"SQLite format 3\000"


def _backups_dir(request: Request) -> Path:
    """Return the backups directory, creating it if needed."""
    data_dir = Path(request.app.state.settings.data_dir)
    backups = data_dir / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    return backups


def _validate_backup_filename(filename: str, backups: Path) -> Path:
    """Validate a backup filename and return the full path. Raises 404/400."""
    if not filename.endswith(".db"):
        raise HTTPException(400, "Invalid backup file")
    path = (backups / filename).resolve()
    try:
        path.relative_to(backups.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    if not path.exists():
        raise HTTPException(404, "Backup not found")
    return path


def _format_size(size: int) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# ---------------------------------------------------------------------------
# Backup endpoints
# ---------------------------------------------------------------------------

@router.post("/backup")
async def api_backup(request: Request):
    """Create a database backup using SQLite online backup API."""
    db = request.app.state.db
    backups = _backups_dir(request)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    filename = f"a-eye-backup-{timestamp}.db"
    backup_path = backups / filename

    try:
        async with aiosqlite.connect(str(backup_path)) as backup_db:
            await db.backup(backup_db)
    except Exception as exc:
        logger.error("Backup failed: %s", exc)
        raise HTTPException(500, f"Backup failed: {exc}")

    size = backup_path.stat().st_size
    logger.info("Database backup created: %s (%s)", filename, _format_size(size))

    return {
        "filename": filename,
        "size": size,
        "size_human": _format_size(size),
        "created_at": timestamp,
    }


@router.get("/backup/list")
async def api_backup_list(request: Request):
    """List available backup files."""
    backups = _backups_dir(request)
    files = sorted(backups.glob("a-eye-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)

    result = []
    for f in files:
        stat = f.stat()
        result.append({
            "filename": f.name,
            "size": stat.st_size,
            "size_human": _format_size(stat.st_size),
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        })

    return {"backups": result}


@router.get("/backup/{filename}/download")
async def api_backup_download(request: Request, filename: str):
    """Download a backup file."""
    backups = _backups_dir(request)
    path = _validate_backup_filename(filename, backups)
    return FileResponse(
        path=str(path),
        filename=filename,
        media_type="application/x-sqlite3",
    )


@router.delete("/backup/{filename}")
async def api_backup_delete(request: Request, filename: str):
    """Delete a backup file."""
    backups = _backups_dir(request)
    path = _validate_backup_filename(filename, backups)

    try:
        os.unlink(path)
    except OSError as exc:
        raise HTTPException(500, f"Failed to delete: {exc}")

    logger.info("Backup deleted: %s", filename)
    return {"deleted": filename}


# ---------------------------------------------------------------------------
# Verify endpoints
# ---------------------------------------------------------------------------

@router.post("/verify")
async def api_verify(request: Request):
    """Verify library integrity: compare database records against files on disk."""
    db = request.app.state.db
    settings = request.app.state.settings
    photos_dir = Path(settings.photos_dir)

    if not photos_dir.exists():
        raise HTTPException(400, "Photos directory does not exist")

    # Build set of relative paths on disk (same logic as watcher._scan)
    disk_files: set[str] = set()
    excluded_folders = settings.excluded_folders_set

    if settings.process_subdirs:
        files = photos_dir.rglob("*")
    else:
        files = photos_dir.glob("*")

    photos_resolved = photos_dir.resolve()

    for file_path in files:
        if not file_path.is_file():
            continue
        if file_path.is_symlink():
            continue
        if file_path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        # Skip dot-prefixed directories
        try:
            rel_parts = file_path.relative_to(photos_dir).parts
            if any(part.startswith(".") for part in rel_parts[:-1]):
                continue
        except ValueError:
            continue
        # Skip excluded folders
        if excluded_folders:
            if "." in excluded_folders:
                continue
            relative = str(file_path.relative_to(photos_dir)).replace("\\", "/")
            parts = relative.split("/")
            if len(parts) == 1 and "__root_files__" in excluded_folders:
                continue
            skip_folder = False
            for i in range(1, len(parts)):
                if "/".join(parts[:i]) in excluded_folders:
                    skip_folder = True
                    break
            if skip_folder:
                continue
        # Verify path is within photos dir
        try:
            file_path.resolve().relative_to(photos_resolved)
        except ValueError:
            continue

        relative_path = str(file_path.relative_to(photos_dir))
        disk_files.add(relative_path)

    # Get all DB records
    cursor = await db.execute("SELECT id, file_path, status FROM images")
    rows = await cursor.fetchall()

    db_paths: dict[str, dict] = {}
    for row in rows:
        db_paths[row["file_path"]] = {"id": row["id"], "file_path": row["file_path"], "status": row["status"]}

    db_path_set = set(db_paths.keys())

    # Compare
    matched = disk_files & db_path_set
    new_on_disk = sorted(disk_files - db_path_set)
    missing_paths = db_path_set - disk_files
    missing_from_disk = [db_paths[p] for p in sorted(missing_paths)]

    logger.info(
        "Library verification: %d matched, %d new on disk, %d missing from disk",
        len(matched), len(new_on_disk), len(missing_from_disk),
    )

    return {
        "matched_count": len(matched),
        "new_on_disk": new_on_disk,
        "new_count": len(new_on_disk),
        "missing_from_disk": missing_from_disk,
        "missing_count": len(missing_from_disk),
    }


@router.post("/verify/cleanup")
async def api_verify_cleanup(request: Request):
    """Remove orphaned database records where the file no longer exists on disk."""
    if not request.app.state.settings.destructive_mode_library:
        raise HTTPException(403, "Destructive mode is not enabled. Enable it in Settings.")
    db = request.app.state.db
    settings = request.app.state.settings
    photos_dir = Path(settings.photos_dir)

    body = await request.json()
    image_ids = body.get("image_ids", [])

    if not image_ids:
        return {"removed": 0}

    removed = 0
    for image_id in image_ids:
        cursor = await db.execute("SELECT file_path FROM images WHERE id = ?", (image_id,))
        row = await cursor.fetchone()
        if not row:
            continue

        # Double-check the file truly doesn't exist
        full_path = photos_dir / row["file_path"]
        if full_path.exists():
            logger.warning("Skipping cleanup of %d — file still exists: %s", image_id, row["file_path"])
            continue

        # Delete rename history first (FK)
        await db.execute("DELETE FROM rename_history WHERE image_id = ?", (image_id,))
        await db.execute("DELETE FROM images WHERE id = ?", (image_id,))
        removed += 1

    await db.commit()
    logger.info("Cleaned up %d orphaned records", removed)
    return {"removed": removed}


# ---------------------------------------------------------------------------
# Restore endpoints
# ---------------------------------------------------------------------------

def _validate_sqlite(path: Path) -> bool:
    """Check if a file is a valid SQLite database."""
    try:
        with open(path, "rb") as f:
            magic = f.read(16)
        return magic == SQLITE_MAGIC
    except OSError:
        return False


async def _reload_app_state(app):
    """Reload in-memory state from the database after a restore."""
    from backend.config import config_update_settings
    from backend.prompts import ensure_defaults, get_active_prompt, STAGE_VISION, STAGE_CONTEXT

    db = app.state.db

    # Run schema migrations (backup might be from older version)
    for col, typedef in [("ai_tags", "TEXT"), ("sidecar_path", "TEXT"),
                          ("quality_flags", "TEXT"), ("processing_context", "TEXT")]:
        try:
            await db.execute(f"ALTER TABLE images ADD COLUMN {col} {typedef}")
        except Exception:
            pass
    await db.commit()

    # Reload settings from DB settings table
    cursor = await db.execute("SELECT key, value FROM settings")
    rows = await cursor.fetchall()
    db_settings = {row["key"]: row["value"] for row in rows}
    if db_settings:
        app.state.settings = config_update_settings(db_settings)

    # Reload prompt templates
    await ensure_defaults(db)
    vision_prompt = await get_active_prompt(db, STAGE_VISION)
    context_prompt = await get_active_prompt(db, STAGE_CONTEXT)
    app.state.ollama.set_templates(
        vision_prompt["content"] if vision_prompt else "",
        context_prompt["content"] if context_prompt else "",
    )

    logger.info("App state reloaded from restored database")


async def _do_restore(app, source_path: Path) -> dict:
    """Core restore logic shared by both restore endpoints."""
    db = app.state.db
    worker = app.state.worker
    watcher = app.state.watcher
    backups = Path(app.state.settings.data_dir) / "backups"
    backups.mkdir(parents=True, exist_ok=True)

    # Validate source is a SQLite database
    if not _validate_sqlite(source_path):
        raise HTTPException(400, "Invalid file — not a SQLite database")

    # Create pre-restore backup
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    pre_restore_name = f"a-eye-pre-restore-{timestamp}.db"
    pre_restore_path = backups / pre_restore_name

    try:
        async with aiosqlite.connect(str(pre_restore_path)) as pre_backup:
            await db.backup(pre_backup)
        logger.info("Pre-restore backup created: %s", pre_restore_name)
    except Exception as exc:
        raise HTTPException(500, f"Failed to create pre-restore backup: {exc}")

    # Pause worker and stop watcher
    worker.pause()
    try:
        await watcher.stop()
    except Exception:
        pass

    # Restore: backup FROM source file INTO live connection
    try:
        async with aiosqlite.connect(str(source_path)) as source:
            await source.backup(db)
        logger.info("Database restored from: %s", source_path.name)
    except Exception as exc:
        logger.error("Restore failed: %s", exc)
        # Try to recover from pre-restore backup
        try:
            async with aiosqlite.connect(str(pre_restore_path)) as recovery:
                await recovery.backup(db)
            logger.info("Recovered from pre-restore backup after failed restore")
        except Exception:
            logger.error("Recovery also failed — database may be in inconsistent state")
        worker.resume()
        raise HTTPException(500, f"Restore failed: {exc}")

    # Reload app state from new database
    try:
        await _reload_app_state(app)
    except Exception as exc:
        logger.error("Failed to reload app state: %s", exc)

    # Resume worker and restart watcher if configured
    worker.resume()
    if app.state.settings.watch_mode:
        try:
            await watcher.start()
        except Exception:
            pass

    return {
        "success": True,
        "restored_from": source_path.name,
        "pre_restore_backup": pre_restore_name,
    }


@router.post("/restore")
async def api_restore(request: Request):
    """Restore database from a backup in the backups folder."""
    if not request.app.state.settings.destructive_mode_library:
        raise HTTPException(403, "Destructive mode is not enabled. Enable it in Settings.")
    body = await request.json()
    filename = body.get("filename", "")

    backups = _backups_dir(request)
    path = _validate_backup_filename(filename, backups)

    return await _do_restore(request.app, path)


@router.post("/restore/upload")
async def api_restore_upload(request: Request, file: UploadFile = File(...)):
    """Restore database from an uploaded backup file."""
    if not request.app.state.settings.destructive_mode_library:
        raise HTTPException(403, "Destructive mode is not enabled. Enable it in Settings.")
    backups = _backups_dir(request)

    # Save uploaded file
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    uploaded_name = f"a-eye-uploaded-{timestamp}.db"
    uploaded_path = backups / uploaded_name

    max_size = 100 * 1024 * 1024  # 100 MB
    try:
        total = 0
        with open(uploaded_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 64)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    f.close()
                    os.unlink(uploaded_path)
                    raise HTTPException(413, "Upload too large — 100 MB limit for database restores")
                f.write(chunk)
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(500, f"Failed to save uploaded file: {exc}")

    try:
        result = await _do_restore(request.app, uploaded_path)
    except HTTPException:
        # Clean up uploaded file on validation failure
        try:
            os.unlink(uploaded_path)
        except OSError:
            pass
        raise

    return result
