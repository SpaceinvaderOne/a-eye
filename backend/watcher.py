from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiosqlite
from watchfiles import Change, awatch

from backend.config import Settings
from backend.database import compute_file_hash, get_image_by_hash, insert_image
from backend.worker import WorkerQueue

logger = logging.getLogger(__name__)

# Image extensions we process
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp",
    ".webp", ".heic", ".heif", ".avif", ".raw", ".cr2",
    ".nef", ".arw", ".dng", ".orf", ".rw2",
}


class FileWatcher:
    """Watches the photos directory for new images and enqueues them."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        settings: Settings,
        worker: WorkerQueue,
    ) -> None:
        self.db = db
        self.settings = settings
        self.worker = worker
        self._running = False
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._scan_in_progress = False
        self._scan_stop_requested = False
        self._last_scan_new = 0
        self._last_scan_skipped = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def scan_in_progress(self) -> bool:
        return self._scan_in_progress

    def request_scan_stop(self) -> None:
        """Abort any in-progress directory scan."""
        self._scan_stop_requested = True

    async def start(self) -> None:
        """Start the watch loop."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._task = asyncio.create_task(self._watch_loop())
        logger.info(
            "File watcher started (subdirs=%s)",
            self.settings.process_subdirs,
        )

    async def stop(self) -> None:
        """Stop the watch loop."""
        self._running = False
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("File watcher stopped")

    async def scan_once(self) -> tuple[int, int, list[int]]:
        """Run a single scan of the photos directory.

        Returns (new_count, skipped_count, new_image_ids).
        """
        self._scan_stop_requested = False
        self._scan_in_progress = True
        try:
            return await self._scan()
        finally:
            self._scan_in_progress = False

    # -- Watch loop: OS-level filesystem event notifications -----------------

    async def _watch_loop(self) -> None:
        """Watch for new files using OS-level notifications (watchfiles).

        Runs an initial full scan, then uses awatch() for real-time detection.
        """
        # Initial full scan to catch anything added while we were stopped
        try:
            new_count, skipped, _ = await self.scan_once()
            if new_count > 0:
                logger.info("Initial scan found %d new images (%d skipped)", new_count, skipped)
        except Exception:
            logger.error("Initial scan failed", exc_info=True)

        photos_dir = Path(self.settings.photos_dir)
        if not photos_dir.exists():
            logger.error("Photos directory does not exist: %s — watch mode stopped", photos_dir)
            return

        # Watch for changes using OS notifications (inotify / FSEvents)
        try:
            logger.info("Watching %s for new files (OS notifications)", photos_dir)
            async for changes in awatch(
                photos_dir,
                watch_filter=self._watch_filter,
                stop_event=self._stop_event,
                recursive=self.settings.process_subdirs,
            ):
                if not self._running:
                    break
                await self._handle_changes(changes, photos_dir)
        except Exception:
            if not self._running:
                return
            logger.error("Filesystem watcher failed — use manual scan", exc_info=True)

    def _watch_filter(self, change: Change, path: str) -> bool:
        """Filter for watchfiles — only notify on new image files."""
        if change != Change.added:
            return False
        return Path(path).suffix.lower() in IMAGE_EXTENSIONS

    async def _handle_changes(self, changes: set, photos_dir: Path) -> None:
        """Process a batch of file change notifications from watchfiles."""
        new_ids: list[int] = []
        photos_resolved = photos_dir.resolve()
        excluded_folders = self.settings.excluded_folders_set

        for _change_type, path_str in changes:
            file_path = Path(path_str)

            if not file_path.is_file():
                continue
            if file_path.is_symlink():
                continue

            # Verify the file is within photos directory
            try:
                file_path.resolve().relative_to(photos_resolved)
            except ValueError:
                logger.warning("Skipping file outside photos directory: %s", file_path)
                continue

            # Skip dot-prefixed directories (e.g. .trash)
            try:
                rel_parts = file_path.relative_to(photos_dir).parts
                if any(part.startswith(".") for part in rel_parts[:-1]):
                    continue
            except ValueError:
                pass

            # Check excluded folders
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

            # Compute hash for dedup
            try:
                file_hash = compute_file_hash(file_path)
            except OSError:
                logger.warning("Cannot read file: %s", file_path)
                continue

            # Check if already known
            if self.settings.skip_processed:
                existing = await get_image_by_hash(self.db, file_hash)
                if existing:
                    continue

            # Insert new image
            relative_path = str(file_path.relative_to(photos_dir))
            file_size = file_path.stat().st_size

            image_id = await insert_image(
                self.db,
                file_path=relative_path,
                original_filename=file_path.name,
                file_hash=file_hash,
                file_size=file_size,
                status="pending",
            )
            new_ids.append(image_id)
            logger.info("Detected new image: %s", file_path.name)

        if new_ids:
            await self.worker.enqueue(new_ids)
            logger.info("Enqueued %d new images from watch event", len(new_ids))

    # -- Full directory scan (used by manual scan + initial startup) --------

    async def _scan(self) -> tuple[int, int, list[int]]:
        """Walk the photos directory, hash files, insert new ones, enqueue for processing."""
        photos_dir = Path(self.settings.photos_dir)
        if not photos_dir.exists():
            logger.warning("Photos directory does not exist: %s", photos_dir)
            return 0, 0, []

        new_ids: list[int] = []
        skipped = 0
        excluded_folders = self.settings.excluded_folders_set

        if self.settings.process_subdirs:
            files = photos_dir.rglob("*")
        else:
            files = photos_dir.glob("*")

        photos_resolved = photos_dir.resolve()

        for file_path in files:
            if self._scan_stop_requested:
                logger.info("Scan aborted by stop request — found %d new images so far", len(new_ids))
                break
            if not file_path.is_file():
                continue
            # Skip symlinks to prevent escaping the photos directory
            if file_path.is_symlink():
                logger.debug("Skipping symlink: %s", file_path)
                continue
            if file_path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            # Skip dot-prefixed directories (e.g. .trash)
            try:
                rel_parts = file_path.relative_to(photos_dir).parts
                if any(part.startswith(".") for part in rel_parts[:-1]):
                    continue
            except ValueError:
                pass
            # Skip excluded folders
            if excluded_folders:
                # Root "." excluded means skip everything
                if "." in excluded_folders:
                    skipped += 1
                    continue
                relative = str(file_path.relative_to(photos_dir)).replace("\\", "/")
                parts = relative.split("/")
                # Skip root-level files if virtual "(root files)" node is excluded
                if len(parts) == 1 and "__root_files__" in excluded_folders:
                    skipped += 1
                    continue
                skip_folder = False
                for i in range(1, len(parts)):  # check each parent dir, not the filename
                    if "/".join(parts[:i]) in excluded_folders:
                        skip_folder = True
                        break
                if skip_folder:
                    skipped += 1
                    continue
            # Verify the resolved path is actually within the photos directory
            try:
                file_path.resolve().relative_to(photos_resolved)
            except ValueError:
                logger.warning("Skipping file outside photos directory: %s", file_path)
                continue

            # Compute hash for dedup
            try:
                file_hash = compute_file_hash(file_path)
            except OSError:
                logger.warning("Cannot read file: %s", file_path)
                continue

            # Check if already known
            if self.settings.skip_processed:
                existing = await get_image_by_hash(self.db, file_hash)
                if existing:
                    skipped += 1
                    continue

            # Relative path for storage
            relative_path = str(file_path.relative_to(photos_dir))
            file_size = file_path.stat().st_size

            image_id = await insert_image(
                self.db,
                file_path=relative_path,
                original_filename=file_path.name,
                file_hash=file_hash,
                file_size=file_size,
                status="pending",
            )
            new_ids.append(image_id)

        # Enqueue all new images for processing
        if new_ids:
            await self.worker.enqueue(new_ids)

        self._last_scan_new = len(new_ids)
        self._last_scan_skipped = skipped
        return len(new_ids), skipped, new_ids
