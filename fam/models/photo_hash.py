"""Photo content hash lookups for upload and attachment deduplication.

Two tables:
  photo_hashes       — content_hash → Drive URL  (sync-time dedup)
  local_photo_hashes — content_hash → local path  (UI attachment dedup)
"""

from datetime import datetime
from typing import Optional

from fam.database.connection import get_connection


def get_drive_url_by_hash(content_hash: str) -> Optional[str]:
    """Look up a Drive URL by content hash. Returns None if not found."""
    conn = get_connection()
    row = conn.execute(
        "SELECT drive_url FROM photo_hashes WHERE content_hash = ?",
        (content_hash,)
    ).fetchone()
    return row['drive_url'] if row else None


def store_photo_hash(content_hash: str, drive_url: str) -> None:
    """Store a content hash → Drive URL mapping.

    Uses INSERT OR REPLACE so re-uploads of the same file
    simply update the URL (e.g. if the old file was deleted
    from Drive and re-uploaded).
    """
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO photo_hashes (content_hash, drive_url, created_at) "
        "VALUES (?, ?, ?)",
        (content_hash, drive_url, datetime.now().isoformat())
    )
    conn.commit()


def get_all_photo_hashes() -> dict[str, str]:
    """Return all stored hashes as {content_hash: drive_url}.

    Used to pre-populate the in-memory cache at the start of
    a sync cycle, avoiding per-file DB queries.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT content_hash, drive_url FROM photo_hashes"
    ).fetchall()
    return {row['content_hash']: row['drive_url'] for row in rows}


# ── Local photo hash registry (cross-transaction UI dedup) ─────


def store_local_photo_hash(content_hash: str, relative_path: str) -> None:
    """Record a content hash → local relative path mapping.

    Called by store_photo() after a new image is saved to disk.
    INSERT OR IGNORE so the first path wins (no overwrites).
    """
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO local_photo_hashes "
        "(content_hash, relative_path, created_at) VALUES (?, ?, ?)",
        (content_hash, relative_path, datetime.now().isoformat())
    )
    conn.commit()


def get_local_path_by_hash(content_hash: str) -> Optional[str]:
    """Look up a local relative path by content hash.

    Returns the relative path (e.g. 'photos/fmnp_42_123.jpg') if
    this content has been stored before, or None.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT relative_path FROM local_photo_hashes WHERE content_hash = ?",
        (content_hash,)
    ).fetchone()
    return row['relative_path'] if row else None
