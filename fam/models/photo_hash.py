"""Photo content hash lookups for upload and attachment deduplication.

Two tables:
  photo_hashes       — content_hash → Drive URL  (sync-time dedup)
  local_photo_hashes — content_hash → local path  (UI attachment dedup)
"""

from typing import Optional
from fam.utils.timezone import eastern_timestamp

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
        (content_hash, drive_url, eastern_timestamp())
    )
    conn.commit()


def delete_photo_hash_by_url(drive_url: str) -> None:
    """Remove any hash entries that point to a dead Drive URL.

    Called when a Drive file is detected as deleted/trashed so the
    hash cache won't short-circuit re-upload with the stale URL.
    """
    conn = get_connection()
    conn.execute(
        "DELETE FROM photo_hashes WHERE drive_url = ?",
        (drive_url,)
    )
    conn.commit()


def _cleanup_orphaned_hashes_for_urls(
        urls: set[str], commit: bool = True) -> int:
    """Drop ``photo_hashes`` cache rows whose ``drive_url`` is in
    *urls* AND is no longer referenced by any active record (non-
    Voided transaction OR non-Deleted FMNP entry).

    Shared core for the void/delete/photo-replace cleanup paths.
    Returns the number of rows removed.

    *commit*: when False the caller is bundling the cleanup into a
    larger transaction.  Default True for standalone use.
    """
    if not urls:
        return 0

    conn = get_connection()
    removed = 0
    for url in urls:
        if not url:
            continue
        active_txn_ref = conn.execute(
            "SELECT 1 FROM payment_line_items pl "
            "JOIN transactions t ON pl.transaction_id = t.id "
            "WHERE t.status != 'Voided' "
            "  AND pl.photo_drive_url LIKE ? "
            "LIMIT 1",
            (f'%{url}%',),
        ).fetchone()
        if active_txn_ref:
            continue
        active_fmnp_ref = conn.execute(
            "SELECT 1 FROM fmnp_entries "
            "WHERE status != 'Deleted' "
            "  AND photo_drive_url LIKE ? "
            "LIMIT 1",
            (f'%{url}%',),
        ).fetchone()
        if active_fmnp_ref:
            continue
        conn.execute(
            "DELETE FROM photo_hashes WHERE drive_url = ?",
            (url,),
        )
        removed += 1

    if commit and removed:
        conn.commit()
    return removed


def cleanup_orphaned_hashes_for_fmnp(
        entry_id: int, commit: bool = True) -> int:
    """FMNP analogue of ``cleanup_orphaned_hashes_for_transaction``.

    Drops ``photo_hashes`` cache rows pointing to URLs unique to
    this FMNP entry's current ``photo_drive_url``.  Called from:

      * ``delete_fmnp_entry`` — after status flip to 'Deleted', so
        the next ``_process_voided_photos`` cycle's VOID_-rename
        of this entry's Drive file doesn't strand future uploads
        of the same image content under the renamed URL.
      * ``update_fmnp_entry`` — before clearing ``photo_drive_url``
        on a photo_path change, so the dedup-cache reflects the
        URLs that will no longer be referenced after the update.

    Photos still shared by another active record stay cached.
    """
    from fam.utils.photo_paths import parse_photo_paths

    conn = get_connection()
    row = conn.execute(
        "SELECT photo_drive_url FROM fmnp_entries "
        "WHERE id = ? "
        "  AND photo_drive_url IS NOT NULL "
        "  AND photo_drive_url != ''",
        (entry_id,),
    ).fetchone()
    if not row:
        return 0
    urls = {u for u in parse_photo_paths(row['photo_drive_url'])
            if u}
    return _cleanup_orphaned_hashes_for_urls(urls, commit=commit)


def cleanup_orphaned_hashes_for_transaction(
        txn_id: int, commit: bool = True) -> int:
    """Drop ``photo_hashes`` rows that pointed to URLs unique to
    transaction *txn_id*.

    Called from ``void_transaction`` so that re-uploading the same
    image to a fresh transaction triggers a fresh Drive upload
    instead of short-circuiting to the now-VOIDed Drive file
    (which the next ``_process_voided_photos`` cycle will rename to
    ``VOID_<orig>``).  Pre-fix (user-reported 2026-05-06):

      1. User uploads receipt → Drive file created, hash cached
      2. User voids the transaction → next sync renames Drive file
         to ``VOID_*``.  hash_cache row still points to it.
      3. User uploads SAME image to a new transaction → upload
         dedup hits hash_cache, skips the upload, and points the
         new transaction's ``photo_drive_url`` at the VOIDed file.
      4. User can't find the image under the new transaction's
         expected name on Drive.

    Photos shared by another active (non-voided) transaction are
    left in the cache — the dedup is still correct for them.
    Returns the number of cache rows removed.

    *commit*: when False the caller is bundling this cleanup into
    a larger transaction (e.g. the void itself) and is responsible
    for committing.
    """
    from fam.utils.photo_paths import parse_photo_paths

    conn = get_connection()
    # Collect every Drive URL referenced by THIS transaction's
    # line items.  ``photo_drive_url`` may be a JSON array (multi-
    # photo line items) so parse defensively.
    rows = conn.execute(
        "SELECT photo_drive_url FROM payment_line_items "
        "WHERE transaction_id = ? "
        "  AND photo_drive_url IS NOT NULL "
        "  AND photo_drive_url != ''",
        (txn_id,),
    ).fetchall()
    urls: set[str] = set()
    for r in rows:
        for u in parse_photo_paths(r['photo_drive_url']):
            if u:
                urls.add(u)
    return _cleanup_orphaned_hashes_for_urls(urls, commit=commit)


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
        (content_hash, relative_path, eastern_timestamp())
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
