from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    current_filename TEXT,
    file_hash TEXT NOT NULL,
    file_size INTEGER,

    exif_date TEXT,
    gps_lat REAL,
    gps_lon REAL,
    location_name TEXT,
    camera_model TEXT,
    exif_raw TEXT,

    vision_description TEXT,
    llm_filename TEXT,
    final_filename TEXT,
    confidence_score REAL,

    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    renamed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rename_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id INTEGER NOT NULL REFERENCES images(id),
    old_path TEXT NOT NULL,
    new_path TEXT NOT NULL,
    renamed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reverted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    stage TEXT NOT NULL,
    content TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_images_status ON images(status);
CREATE INDEX IF NOT EXISTS idx_images_file_hash ON images(file_hash);
CREATE INDEX IF NOT EXISTS idx_images_file_path ON images(file_path);
CREATE INDEX IF NOT EXISTS idx_rename_history_image_id ON rename_history(image_id);
"""


async def init_db(db_path: str) -> aiosqlite.Connection:
    """Open (or create) the SQLite database and ensure tables exist."""
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.executescript(_SCHEMA)
    await db.commit()

    # Schema migrations — add columns that may not exist yet
    for col, typedef in [("ai_tags", "TEXT"), ("sidecar_path", "TEXT"), ("quality_flags", "TEXT"), ("processing_context", "TEXT"), ("in_queue", "INTEGER DEFAULT 0"), ("retry_count", "INTEGER DEFAULT 0")]:
        try:
            await db.execute(f"ALTER TABLE images ADD COLUMN {col} {typedef}")
        except Exception:
            pass  # Column already exists
    await db.commit()

    logger.info("Database initialized at %s", db_path)
    return db


async def recover_stuck_images(db: aiosqlite.Connection, max_retries: int = 3) -> tuple[int, int]:
    """Reset images stuck in 'processing' after a crash.

    Images under the retry limit are reset to 'pending' with retry_count
    incremented.  Images at/over the limit are moved to 'error'.

    Returns (recovered_count, failed_count).
    """
    # Count before we modify anything
    cursor = await db.execute(
        "SELECT COUNT(*) FROM images WHERE status = 'processing' AND retry_count < ?",
        (max_retries,),
    )
    recovered = (await cursor.fetchone())[0]

    cursor = await db.execute(
        "SELECT COUNT(*) FROM images WHERE status = 'processing' AND retry_count >= ?",
        (max_retries,),
    )
    failed = (await cursor.fetchone())[0]

    if recovered:
        await db.execute(
            "UPDATE images SET status = 'pending', retry_count = retry_count + 1 "
            "WHERE status = 'processing' AND retry_count < ?",
            (max_retries,),
        )

    if failed:
        await db.execute(
            "UPDATE images SET status = 'error', error_message = ? "
            "WHERE status = 'processing' AND retry_count >= ?",
            (f"Failed after {max_retries} recovery attempts", max_retries),
        )

    if recovered or failed:
        await db.commit()

    return recovered, failed


async def get_pending_image_ids(db: aiosqlite.Connection) -> list[int]:
    """Get IDs of all images with status='pending', oldest first."""
    cursor = await db.execute(
        "SELECT id FROM images WHERE status = 'pending' ORDER BY created_at",
    )
    rows = await cursor.fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# File hashing
# ---------------------------------------------------------------------------

def compute_file_hash(path: Path) -> str:
    """Compute a fast identity hash: first 64KB + last 64KB + file size.

    More collision-resistant than first-64KB-only, especially for burst-mode
    photos from the same camera in the same second.
    """
    size = path.stat().st_size
    h = hashlib.sha256()
    h.update(str(size).encode())

    chunk_size = 65536  # 64KB

    with open(path, "rb") as f:
        # First 64KB
        h.update(f.read(chunk_size))

        # Last 64KB (if file is large enough that it differs from the first chunk)
        if size > chunk_size * 2:
            f.seek(-chunk_size, 2)
            h.update(f.read(chunk_size))

    return h.hexdigest()


# ---------------------------------------------------------------------------
# Image CRUD
# ---------------------------------------------------------------------------

# Defence-in-depth: only these column names may be used in dynamic SQL.
_ALLOWED_COLUMNS = frozenset({
    "file_path", "original_filename", "current_filename", "file_hash", "file_size",
    "exif_date", "gps_lat", "gps_lon", "location_name", "camera_model", "exif_raw",
    "vision_description", "llm_filename", "final_filename", "confidence_score",
    "status", "error_message", "created_at", "processed_at", "renamed_at",
    "ai_tags", "sidecar_path", "quality_flags", "processing_context", "in_queue",
    "retry_count",
})


def _check_columns(kwargs: dict[str, Any]) -> None:
    """Reject any column names not in the whitelist."""
    bad = set(kwargs.keys()) - _ALLOWED_COLUMNS
    if bad:
        raise ValueError(f"Invalid column names: {bad}")


async def insert_image(db: aiosqlite.Connection, **kwargs: Any) -> int:
    """Insert an image row and return its ID."""
    _check_columns(kwargs)
    # Serialize exif_raw dict to JSON string if present
    if "exif_raw" in kwargs and isinstance(kwargs["exif_raw"], dict):
        kwargs["exif_raw"] = json.dumps(kwargs["exif_raw"])
    if "ai_tags" in kwargs and isinstance(kwargs["ai_tags"], list):
        kwargs["ai_tags"] = json.dumps(kwargs["ai_tags"])
    if "quality_flags" in kwargs and isinstance(kwargs["quality_flags"], list):
        kwargs["quality_flags"] = json.dumps(kwargs["quality_flags"])

    columns = ", ".join(kwargs.keys())
    placeholders = ", ".join(f":{k}" for k in kwargs.keys())
    sql = f"INSERT INTO images ({columns}) VALUES ({placeholders})"

    cursor = await db.execute(sql, kwargs)
    await db.commit()
    return cursor.lastrowid


async def update_image(db: aiosqlite.Connection, image_id: int, **kwargs: Any) -> None:
    """Update fields on an image row."""
    _check_columns(kwargs)
    if "exif_raw" in kwargs and isinstance(kwargs["exif_raw"], dict):
        kwargs["exif_raw"] = json.dumps(kwargs["exif_raw"])
    if "ai_tags" in kwargs and isinstance(kwargs["ai_tags"], list):
        kwargs["ai_tags"] = json.dumps(kwargs["ai_tags"])
    if "quality_flags" in kwargs and isinstance(kwargs["quality_flags"], list):
        kwargs["quality_flags"] = json.dumps(kwargs["quality_flags"])

    set_clause = ", ".join(f"{k} = :{k}" for k in kwargs.keys())
    kwargs["_id"] = image_id
    sql = f"UPDATE images SET {set_clause} WHERE id = :_id"

    await db.execute(sql, kwargs)
    await db.commit()


async def get_image(db: aiosqlite.Connection, image_id: int) -> dict | None:
    """Fetch a single image by ID."""
    cursor = await db.execute("SELECT * FROM images WHERE id = ?", (image_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


async def get_image_by_hash(db: aiosqlite.Connection, file_hash: str) -> dict | None:
    """Find an image by its file hash (for dedup/skip detection)."""
    cursor = await db.execute(
        "SELECT * FROM images WHERE file_hash = ? LIMIT 1", (file_hash,)
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


async def get_image_by_path(db: aiosqlite.Connection, file_path: str) -> dict | None:
    """Find an image by its current file path."""
    cursor = await db.execute(
        "SELECT * FROM images WHERE file_path = ? LIMIT 1", (file_path,)
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


async def list_images(
    db: aiosqlite.Connection,
    status: str | None = None,
    folder: str | None = None,
    offset: int = 0,
    limit: int = 50,
    sort: str = "created_at",
    sort_dir: str = "desc",
) -> list[dict]:
    """List images with optional filtering, sorting, and pagination."""
    conditions = []
    params: dict[str, Any] = {}

    if status == "queued":
        conditions.append("in_queue = 1")
    elif status == "quality_issues":
        conditions.append("quality_flags IS NOT NULL AND quality_flags != '[]' AND quality_flags != ''")
    elif status:
        conditions.append("status = :status")
        params["status"] = status

    if folder:
        conditions.append("file_path LIKE :folder")
        params["folder"] = f"{folder}%"

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Whitelist sortable columns
    allowed_sorts = {"created_at", "exif_date", "original_filename", "current_filename", "status", "confidence_score"}
    if sort not in allowed_sorts:
        sort = "created_at"
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"

    sql = f"""
        SELECT * FROM images {where}
        ORDER BY {sort} {direction}
        LIMIT :limit OFFSET :offset
    """
    params["limit"] = limit
    params["offset"] = offset

    cursor = await db.execute(sql, params)
    rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def count_images(db: aiosqlite.Connection, status: str | None = None) -> int:
    """Count images, optionally filtered by status."""
    if status == "queued":
        cursor = await db.execute(
            "SELECT COUNT(*) FROM images WHERE in_queue = 1"
        )
    elif status == "quality_issues":
        cursor = await db.execute(
            "SELECT COUNT(*) FROM images WHERE quality_flags IS NOT NULL AND quality_flags != '[]' AND quality_flags != ''"
        )
    elif status:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM images WHERE status = ?", (status,)
        )
    else:
        cursor = await db.execute("SELECT COUNT(*) FROM images")
    row = await cursor.fetchone()
    return row[0]


async def get_stats(db: aiosqlite.Connection) -> dict[str, int]:
    """Get count of images by status for the dashboard."""
    cursor = await db.execute(
        "SELECT status, COUNT(*) as cnt FROM images GROUP BY status"
    )
    rows = await cursor.fetchall()
    stats = {row["status"]: row["cnt"] for row in rows}
    stats["total"] = sum(stats.values())

    # Quality issues count (cross-cutting, not a status)
    cursor = await db.execute(
        "SELECT COUNT(*) FROM images WHERE quality_flags IS NOT NULL AND quality_flags != '[]' AND quality_flags != ''"
    )
    qi_row = await cursor.fetchone()
    stats["quality_issues"] = qi_row[0] if qi_row else 0

    # Queued count (cross-cutting, not a status)
    cursor = await db.execute("SELECT COUNT(*) FROM images WHERE in_queue = 1")
    q_row = await cursor.fetchone()
    stats["queued"] = q_row[0] if q_row else 0

    return stats


async def get_outcome_stats(db: aiosqlite.Connection) -> dict[str, int]:
    """Get outcome-based counts for the dashboard."""
    cursor = await db.execute("""
        SELECT
            SUM(CASE WHEN status IN ('renamed','completed','proposed','error','skipped') THEN 1 ELSE 0 END),
            SUM(CASE WHEN status = 'renamed' THEN 1 ELSE 0 END),
            SUM(CASE WHEN sidecar_path IS NOT NULL AND vision_description IS NOT NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN sidecar_path IS NOT NULL AND ai_tags IS NOT NULL AND ai_tags != '[]' THEN 1 ELSE 0 END),
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)
        FROM images
    """)
    row = await cursor.fetchone()
    return {
        "processed": row[0] or 0,
        "renamed": row[1] or 0,
        "descriptions_written": row[2] or 0,
        "tags_written": row[3] or 0,
        "catalogued": row[4] or 0,
    }


# ---------------------------------------------------------------------------
# Rename history
# ---------------------------------------------------------------------------

async def insert_rename_history(
    db: aiosqlite.Connection, image_id: int, old_path: str, new_path: str
) -> int:
    cursor = await db.execute(
        "INSERT INTO rename_history (image_id, old_path, new_path) VALUES (?, ?, ?)",
        (image_id, old_path, new_path),
    )
    await db.commit()
    return cursor.lastrowid


async def get_rename_history(
    db: aiosqlite.Connection,
    image_id: int | None = None,
    offset: int = 0,
    limit: int = 50,
) -> list[dict]:
    """Get rename history, optionally filtered by image_id."""
    if image_id is not None:
        cursor = await db.execute(
            "SELECT * FROM rename_history WHERE image_id = ? ORDER BY renamed_at DESC LIMIT ? OFFSET ?",
            (image_id, limit, offset),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM rename_history ORDER BY renamed_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def count_rename_history(db: aiosqlite.Connection) -> int:
    """Count total rename history entries."""
    cursor = await db.execute("SELECT COUNT(*) FROM rename_history")
    row = await cursor.fetchone()
    return row[0]


async def mark_rename_reverted(db: aiosqlite.Connection, history_id: int) -> None:
    await db.execute(
        "UPDATE rename_history SET reverted_at = CURRENT_TIMESTAMP WHERE id = ?",
        (history_id,),
    )
    await db.commit()


async def mark_image_history_reverted(db: aiosqlite.Connection, image_id: int) -> None:
    """Mark all non-reverted history entries for an image as reverted."""
    await db.execute(
        "UPDATE rename_history SET reverted_at = CURRENT_TIMESTAMP WHERE image_id = ? AND reverted_at IS NULL",
        (image_id,),
    )
    await db.commit()


async def delete_reverted_history(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("DELETE FROM rename_history WHERE reverted_at IS NOT NULL")
    await db.commit()
    return cursor.rowcount


async def delete_all_history(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("DELETE FROM rename_history")
    await db.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

async def get_setting(db: aiosqlite.Connection, key: str) -> str | None:
    cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = await cursor.fetchone()
    return row["value"] if row else None


async def set_setting(db: aiosqlite.Connection, key: str, value: str) -> None:
    await db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )
    await db.commit()


async def get_all_settings(db: aiosqlite.Connection) -> dict[str, str]:
    cursor = await db.execute("SELECT key, value FROM settings")
    rows = await cursor.fetchall()
    return {row["key"]: row["value"] for row in rows}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: aiosqlite.Row) -> dict:
    """Convert a Row to a plain dict, deserializing JSON fields."""
    d = dict(row)
    if d.get("exif_raw") and isinstance(d["exif_raw"], str):
        try:
            d["exif_raw"] = json.loads(d["exif_raw"])
        except (json.JSONDecodeError, TypeError):
            pass
    if d.get("ai_tags") and isinstance(d["ai_tags"], str):
        try:
            d["ai_tags"] = json.loads(d["ai_tags"])
        except (json.JSONDecodeError, TypeError):
            d["ai_tags"] = []
    if d.get("quality_flags") and isinstance(d["quality_flags"], str):
        try:
            d["quality_flags"] = json.loads(d["quality_flags"])
        except (json.JSONDecodeError, TypeError):
            d["quality_flags"] = []
    return d
