from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from backend.config import Settings
from backend.database import (
    get_image,
    insert_rename_history,
    update_image,
)
from backend.filename import ensure_unique
from backend.ollama_client import OllamaClient
from backend.date_extract import extract_date_from_text
from backend.pipeline import process_image
from backend.xmp_writer import rename_xmp_sidecar, write_xmp_sidecar

logger = logging.getLogger(__name__)


class WorkerQueue:
    """Async background processing queue for image pipeline jobs."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        settings: Settings,
        ollama: OllamaClient,
    ) -> None:
        self.db = db
        self.settings = settings
        self.ollama = ollama
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._processed_count = 0
        self._error_count = 0
        self._resume_event: asyncio.Event = asyncio.Event()
        self._resume_event.set()  # Start unpaused
        self._stop_requested = False

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    @property
    def processed_count(self) -> int:
        return self._processed_count

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return not self._resume_event.is_set()

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def pause(self) -> None:
        """Pause processing — workers finish current image then wait."""
        self._resume_event.clear()
        logger.info("Worker queue paused")

    def resume(self) -> None:
        """Resume processing."""
        self._resume_event.set()
        logger.info("Worker queue resumed")

    def request_stop(self) -> int:
        """Signal workers to stop after finishing their current image.

        Drains the queue — pending images stay as 'pending' in DB.
        Returns the number of queued items drained.
        """
        self._stop_requested = True
        drained = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                drained += 1
            except asyncio.QueueEmpty:
                break
        logger.info("Stop requested — drained %d queued images", drained)
        return drained

    def clear_stop(self) -> None:
        """Clear the stop flag so new work can be enqueued."""
        self._stop_requested = False

    async def start(self) -> None:
        """Start worker coroutines."""
        if self._running:
            return
        self._running = True
        for i in range(self.settings.concurrent_workers):
            task = asyncio.create_task(self._worker_loop(i))
            self._tasks.append(task)
        logger.info("Started %d worker(s)", self.settings.concurrent_workers)

    async def stop(self) -> None:
        """Gracefully stop all workers."""
        self._running = False
        self._resume_event.set()  # Unblock paused workers so they can exit
        # Put sentinel values to unblock workers
        for _ in self._tasks:
            await self._queue.put(-1)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Workers stopped")

    async def enqueue(self, image_ids: list[int]) -> int:
        """Add image IDs to the processing queue. Returns count enqueued."""
        self._stop_requested = False  # Clear any previous stop
        count = 0
        for image_id in image_ids:
            await self._queue.put(image_id)
            count += 1
        logger.info("Enqueued %d images for processing", count)
        return count

    async def _worker_loop(self, worker_id: int) -> None:
        """Worker loop: pull from queue, process, update DB."""
        logger.debug("Worker %d started", worker_id)
        while self._running:
            # Block here if paused (schedule window closed)
            try:
                await asyncio.wait_for(self._resume_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                image_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            if image_id == -1:  # Sentinel for shutdown
                break

            if self._stop_requested:
                self._queue.task_done()
                continue

            try:
                await self._process_one(image_id, worker_id)
                self._processed_count += 1
            except Exception as exc:
                self._error_count += 1
                logger.error(
                    "Worker %d: unhandled error processing image %d",
                    worker_id, image_id, exc_info=True,
                )
                try:
                    await update_image(
                        self.db, image_id,
                        status="error",
                        error_message=f"Unhandled error: {exc}",
                        processed_at=_now(),
                    )
                except Exception:
                    logger.error("Failed to mark image %d as error", image_id, exc_info=True)
            finally:
                self._queue.task_done()

        logger.debug("Worker %d stopped", worker_id)

    async def _process_one(self, image_id: int, worker_id: int) -> None:
        """Process a single image through the pipeline and handle rename logic."""
        image = await get_image(self.db, image_id)
        if image is None:
            logger.warning("Worker %d: image %d not found in DB", worker_id, image_id)
            return

        # Guard against double-processing (e.g. duplicate enqueue)
        if image["status"] != "pending":
            logger.debug(
                "Worker %d: image %d not in status 'pending' (was '%s'), skipping",
                worker_id, image_id, image["status"],
            )
            return

        photos_dir = Path(self.settings.photos_dir)
        file_path = (photos_dir / image["file_path"]).resolve()
        # Verify the file is within the photos directory
        try:
            file_path.relative_to(photos_dir.resolve())
        except ValueError:
            await update_image(self.db, image_id, status="error", error_message="File outside photos directory")
            return
        if not file_path.exists():
            await update_image(
                self.db, image_id,
                status="error",
                error_message="File not found on disk",
            )
            return

        # Mark as processing
        await update_image(self.db, image_id, status="processing")

        logger.info(
            "Worker %d: processing image %d (%s)", worker_id, image_id, image["file_path"]
        )

        # Run the pipeline
        result = await process_image(
            file_path, self.settings, self.ollama,
            processing_context=image.get("processing_context"),
        )

        if result.error:
            await update_image(
                self.db, image_id,
                status="error",
                error_message=result.error,
                processed_at=_now(),
            )
            return

        # Store pipeline results
        update_fields: dict = {
            "vision_description": result.vision_description,
            "final_filename": result.final_filename,
            "confidence_score": result.confidence_score,
            "processed_at": _now(),
        }

        if result.ai_tags:
            update_fields["ai_tags"] = result.ai_tags

        # Always store quality_flags (empty list = no issues detected)
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
                logger.info("Image %d: extracted date %s from AI text", image_id, extracted)

        # Decide what to do based on rename mode
        if self.settings.process_rename and not self.settings.catalogue_mode:
            mode = self.settings.rename_mode

            if mode == "auto":
                # Auto-rename immediately
                await update_image(self.db, image_id, status="approved", **update_fields)
                await self._do_rename(image_id, image, result.final_filename, file_path)

            elif mode == "auto-low-confidence":
                if result.confidence_score >= self.settings.confidence_threshold:
                    await update_image(self.db, image_id, status="approved", **update_fields)
                    await self._do_rename(image_id, image, result.final_filename, file_path)
                else:
                    # Flag for manual review
                    await update_image(self.db, image_id, status="proposed", **update_fields)
                    logger.info(
                        "Image %d flagged for review (confidence %.2f < %.2f)",
                        image_id, result.confidence_score, self.settings.confidence_threshold,
                    )

            else:
                # review mode (default) — just propose, don't rename
                await update_image(self.db, image_id, status="proposed", **update_fields)
        else:
            # Rename disabled — mark as completed (metadata-only processing)
            await update_image(self.db, image_id, status="completed", **update_fields)

        # Write XMP sidecar if any metadata modes are enabled (skip on read-only mount)
        photos_readonly = getattr(self, "_photos_readonly", False)
        want_desc = self.settings.process_write_description
        want_tags = self.settings.process_write_tags
        if result.vision_description and (want_desc or want_tags) and not photos_readonly and not self.settings.catalogue_mode:
            try:
                # Refresh image to get current file_path (may have been renamed above)
                image_refreshed = await get_image(self.db, image_id)
                current_file = photos_dir / image_refreshed["file_path"]

                sidecar = write_xmp_sidecar(
                    image_path=current_file,
                    description=result.vision_description if want_desc else None,
                    tags=result.ai_tags if want_tags else None,
                    date=(result.metadata.datetime_full if result.metadata else None)
                    or image_refreshed.get("exif_date"),
                )
                sidecar_relative = str(sidecar.relative_to(photos_dir))
                await update_image(self.db, image_id, sidecar_path=sidecar_relative)
            except Exception as exc:
                logger.error(
                    "Failed to write XMP sidecar for image %d", image_id, exc_info=True,
                )
                try:
                    await update_image(self.db, image_id, error_message=f"XMP sidecar failed: {exc}")
                except Exception:
                    pass

    async def _do_rename(
        self, image_id: int, image: dict, new_name: str, current_path: Path
    ) -> None:
        """Actually rename the file on disk."""
        if self.settings.dry_run:
            logger.info("DRY RUN: would rename %s → %s", current_path.name, new_name)
            await update_image(
                self.db, image_id,
                status="renamed",
                current_filename=new_name,
                renamed_at=_now(),
            )
            return

        # Build the new path (preserve extension and directory)
        extension = current_path.suffix
        new_path = current_path.parent / f"{new_name}{extension}"
        new_path = ensure_unique(new_path)

        try:
            # os.rename preserves metadata — don't use copy+delete
            os.rename(current_path, new_path)
            rename_xmp_sidecar(current_path, new_path)

            # Update DB
            new_relative = str(new_path.relative_to(self.settings.photos_dir))
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

            logger.info("Renamed: %s → %s", current_path.name, new_path.name)

        except OSError as exc:
            await update_image(
                self.db, image_id,
                status="error",
                error_message=f"Rename failed: {exc}",
            )
            logger.error("Rename failed for %s: %s", current_path, exc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
