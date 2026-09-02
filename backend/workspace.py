"""Workspace module — temporary processing area for upload-rename-download workflows.

Files are uploaded to a workspace directory, processed through the AI pipeline,
reviewed/approved, and downloaded as a zip. The workspace uses its own SQLite
database to keep temporary files completely isolated from the main library.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from backend.config import Settings
from backend.database import (
    compute_file_hash,
    count_images,
    get_image,
    get_image_by_hash,
    get_stats,
    init_db,
    insert_image,
    insert_rename_history,
    list_images,
    update_image,
)
from backend.filename import ensure_unique
from backend.ollama_client import OllamaClient
from backend.date_extract import extract_date_from_text
from backend.pipeline import process_image
from backend.watcher import IMAGE_EXTENSIONS

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Workspace:
    """Manages the temporary processing workspace lifecycle."""

    def __init__(
        self,
        workspace_dir: Path,
        settings: Settings,
        ollama: OllamaClient,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.settings = settings
        self.ollama = ollama
        self.db: aiosqlite.Connection | None = None
        self._processing = False
        self._process_task: asyncio.Task | None = None

    @property
    def is_processing(self) -> bool:
        return self._processing

    def request_stop(self) -> None:
        """Stop workspace processing after current image finishes."""
        self._processing = False

    async def init(self) -> None:
        """Create workspace directory and database if they don't exist."""
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        (self.workspace_dir / "thumbnails").mkdir(exist_ok=True)

        db_path = str(self.workspace_dir / "workspace.db")
        self.db = await init_db(db_path)

        # Reset any workspace images stuck in 'processing' from a previous crash
        cursor = await self.db.execute(
            "UPDATE images SET status = 'pending' WHERE status = 'processing'"
        )
        if cursor.rowcount:
            await self.db.commit()
            logger.info("Workspace recovery: reset %d stuck image(s) to pending", cursor.rowcount)

        logger.info("Workspace initialized at %s", self.workspace_dir)

    async def close(self) -> None:
        """Stop processing and close the database."""
        self._processing = False
        if self._process_task and not self._process_task.done():
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass
            self._process_task = None
        if self.db:
            await self.db.close()
            self.db = None

    async def get_stats(self) -> dict:
        """Return workspace file counts by status + processing flag."""
        if not self.db:
            return {"total": 0}
        stats = await get_stats(self.db)
        stats["processing_active"] = self._processing
        return stats

    async def scan_workspace(self) -> int:
        """Scan workspace directory for image files. Returns count of new images."""
        if not self.db:
            return 0

        new_count = 0
        for file_path in self.workspace_dir.iterdir():
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            if file_path.name.startswith("."):
                continue
            # Skip workspace DB and zip files
            if file_path.suffix in (".db", ".zip", ".db-journal", ".db-wal"):
                continue

            try:
                file_hash = compute_file_hash(file_path)
            except OSError:
                continue

            existing = await get_image_by_hash(self.db, file_hash)
            if existing:
                continue

            relative_path = file_path.name  # Flat directory, just filename
            file_size = file_path.stat().st_size

            await insert_image(
                self.db,
                file_path=relative_path,
                original_filename=file_path.name,
                file_hash=file_hash,
                file_size=file_size,
                status="pending",
            )
            new_count += 1
            logger.info("Workspace: found new image %s", file_path.name)

        return new_count

    async def start_processing(self) -> None:
        """Launch background processing for all pending workspace images."""
        if self._processing:
            return
        self._process_task = asyncio.create_task(self._process_pending())

    async def _process_pending(self) -> None:
        """Process all pending images sequentially (no worker queue)."""
        if not self.db:
            return
        self._processing = True
        try:
            while self._processing:
                # Fetch next pending image
                pending = await list_images(self.db, status="pending", limit=1, sort="id", sort_dir="asc")
                if not pending:
                    break

                image = pending[0]
                image_id = image["id"]
                file_path = (self.workspace_dir / image["file_path"]).resolve()
                try:
                    file_path.relative_to(self.workspace_dir.resolve())
                except ValueError:
                    logger.warning("Workspace: path traversal blocked for image %d", image_id)
                    await update_image(self.db, image_id, status="error", error_message="Invalid file path")
                    continue

                if not file_path.exists():
                    await update_image(self.db, image_id, status="error", error_message="File not found")
                    continue

                await update_image(self.db, image_id, status="processing")

                try:
                    result = await process_image(
                        file_path, self.settings, self.ollama,
                        processing_context=image.get("processing_context"),
                    )
                except Exception as exc:
                    await update_image(
                        self.db, image_id, status="error",
                        error_message=f"Processing failed: {exc}",
                        processed_at=_now(),
                    )
                    logger.error("Workspace: processing failed for %s: %s", file_path.name, exc)
                    continue

                if result.error:
                    await update_image(
                        self.db, image_id, status="error",
                        error_message=result.error, processed_at=_now(),
                    )
                    continue

                # Store results — always go to proposed (workspace uses review flow)
                update_fields: dict = {
                    "vision_description": result.vision_description,
                    "final_filename": result.final_filename,
                    "confidence_score": result.confidence_score,
                    "processed_at": _now(),
                    "status": "proposed",
                }
                if result.ai_tags:
                    update_fields["ai_tags"] = result.ai_tags
                update_fields["quality_flags"] = result.quality_flags
                if result.metadata:
                    update_fields["exif_date"] = result.metadata.date
                    update_fields["gps_lat"] = result.metadata.gps_lat
                    update_fields["gps_lon"] = result.metadata.gps_lon
                    update_fields["camera_model"] = result.metadata.camera_model
                    update_fields["exif_raw"] = result.metadata.raw
                if result.location_name:
                    update_fields["location_name"] = result.location_name

                # Date extraction from AI text (when EXIF date is missing)
                if update_fields.get("exif_date") is None and (
                    result.vision_description or result.ai_tags
                ):
                    extracted = extract_date_from_text(
                        result.vision_description, result.ai_tags or [],
                        processing_context=image.get("processing_context"),
                    )
                    if extracted:
                        update_fields["exif_date"] = extracted
                        logger.info("Workspace image %d: extracted date %s from AI text", image_id, extracted)

                await update_image(self.db, image_id, **update_fields)
                logger.info("Workspace: processed %s → %s", file_path.name, result.final_filename)

        except asyncio.CancelledError:
            logger.info("Workspace processing cancelled")
        except Exception:
            logger.error("Workspace processing error", exc_info=True)
        finally:
            self._processing = False

    async def approve_image(self, image_id: int, filename: str | None = None) -> dict:
        """Approve a workspace image — rename the file on disk."""
        if not self.db:
            raise ValueError("Workspace not initialized")

        image = await get_image(self.db, image_id)
        if not image:
            raise ValueError("Image not found")

        new_name = filename or image.get("final_filename")
        if not new_name:
            raise ValueError("No proposed filename")

        current_path = (self.workspace_dir / image["file_path"]).resolve()
        try:
            current_path.relative_to(self.workspace_dir.resolve())
        except ValueError:
            raise ValueError("Invalid file path — outside workspace")
        if not current_path.exists():
            raise ValueError("File not found on disk")

        extension = current_path.suffix
        new_path = (self.workspace_dir / f"{new_name}{extension}").resolve()
        try:
            new_path.relative_to(self.workspace_dir.resolve())
        except ValueError:
            raise ValueError("Invalid filename — path traversal detected")
        new_path = ensure_unique(new_path)

        try:
            os.rename(current_path, new_path)
        except OSError as exc:
            await update_image(self.db, image_id, status="error", error_message=f"Rename failed: {exc}")
            raise

        new_relative = new_path.name
        await insert_rename_history(
            self.db, image_id,
            old_path=image["file_path"],
            new_path=new_relative,
        )
        await update_image(
            self.db, image_id,
            status="renamed",
            file_path=new_relative,
            current_filename=new_path.name,
            renamed_at=_now(),
        )

        return {"status": "renamed", "new_name": new_path.name}

    async def delete_image(self, image_id: int) -> dict:
        """Permanently delete a workspace image — file, thumbnail, and DB record."""
        if not self.db:
            raise ValueError("Workspace not initialized")

        image = await get_image(self.db, image_id)
        if not image:
            raise ValueError("Image not found")

        # Remove file and its XMP sidecar from disk
        file_path = (self.workspace_dir / image["file_path"]).resolve()
        try:
            file_path.relative_to(self.workspace_dir.resolve())
        except ValueError:
            raise ValueError("Invalid file path")
        if file_path.exists():
            try:
                file_path.unlink()
            except OSError as exc:
                logger.warning("Failed to delete file %s: %s", file_path, exc)
        xmp_path = file_path.with_suffix(file_path.suffix + ".xmp")
        if xmp_path.exists():
            try:
                xmp_path.unlink()
            except OSError:
                pass

        # Remove thumbnail
        thumb_path = self.workspace_dir / "thumbnails" / f"{image_id}.jpg"
        if thumb_path.exists():
            try:
                thumb_path.unlink()
            except OSError:
                pass

        # Delete DB record
        await self.db.execute("DELETE FROM rename_history WHERE image_id = ?", (image_id,))
        await self.db.execute("DELETE FROM images WHERE id = ?", (image_id,))
        await self.db.commit()

        logger.info("Workspace: deleted image %d (%s)", image_id, image["file_path"])
        return {"deleted": True}

    async def create_download_zip(self) -> io.BytesIO:
        """Create a zip of all renamed files in the workspace."""
        if not self.db:
            raise ValueError("Workspace not initialized")

        images = await list_images(self.db, status="renamed", limit=10000)
        if not images:
            raise ValueError("No renamed files to download")

        buffer = io.BytesIO()
        seen_names: set[str] = set()

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
            for image in images:
                file_path = (self.workspace_dir / image["file_path"]).resolve()
                try:
                    file_path.relative_to(self.workspace_dir.resolve())
                except ValueError:
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
        return buffer

    async def clear(self) -> dict:
        """Delete all workspace files and reset the database."""
        # Stop any processing
        self._processing = False
        if self._process_task and not self._process_task.done():
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass
            self._process_task = None

        # Close DB
        if self.db:
            await self.db.close()
            self.db = None

        # Delete all files in workspace (preserve the directory itself)
        deleted = 0
        for item in self.workspace_dir.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                deleted += 1
            except OSError:
                logger.warning("Failed to delete %s", item)

        # Reinitialize
        await self.init()

        logger.info("Workspace cleared (%d items removed)", deleted)
        return {"deleted": deleted}
