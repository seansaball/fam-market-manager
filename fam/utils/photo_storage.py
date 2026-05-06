"""Local photo storage — copy and optionally resize check/receipt photos."""

import hashlib
import logging
import os
import shutil
import tempfile
import time

logger = logging.getLogger('fam.utils.photo_storage')

MAX_DIMENSION = 1920  # Longest side, pixels
JPEG_QUALITY = 85
PHOTOS_SUBDIR = 'photos'


def _atomic_write_bytes(target_path: str, data: bytes) -> None:
    """Write ``data`` to ``target_path`` atomically.

    A reader who happens to look at the directory MUST see either
    the prior file (if any) OR the complete new file — never a
    half-written one.  This matters in two production scenarios:

      1. The concurrent ``write_ledger_backup`` could observe a
         half-saved photo while computing aggregates.
      2. A power loss mid-write would leave a corrupt file at the
         target name; the rename-pattern leaves a temp orphan
         instead, which the next cleanup pass collects.

    Writes to a tempfile in the SAME directory (so the rename is
    a metadata-only atomic rename, not a cross-filesystem copy).
    Then ``os.replace`` (cross-platform atomic rename).
    """
    target_dir = os.path.dirname(target_path)
    fd, tmp_path = tempfile.mkstemp(
        prefix='.tmp_photo_', suffix='.partial', dir=target_dir)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # fsync unsupported on some filesystems — fall
                # through.  os.replace still gives atomic rename.
                pass
        os.replace(tmp_path, target_path)
    except Exception:
        # Clean up the temp file if we never replaced.
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


def get_photos_dir() -> str:
    """Return the photos directory, creating it if needed."""
    from fam.app import get_data_dir
    photos_dir = os.path.join(get_data_dir(), PHOTOS_SUBDIR)
    os.makedirs(photos_dir, exist_ok=True)
    return photos_dir


def store_photo(source_path: str, entry_id: int, prefix: str = 'fmnp') -> str:
    """Copy a photo into the local photos directory.

    Resizes if larger than MAX_DIMENSION on the longest side.
    Returns the relative path (e.g., 'photos/fmnp_42_1709912345.jpg')
    for storage in the database.

    *prefix* distinguishes photo types: 'fmnp' for FMNP check photos,
    'pay' for payment-screen receipt photos.

    After storing, the **source** file's SHA-256 hash is recorded in
    ``local_photo_hashes`` so future attachments of the same image
    are detected across transactions.

    v1.9.10 follow-up (2026-05-01): writes are ATOMIC.  We save the
    image to a temporary path under the photos directory and only
    ``os.replace`` it to the final name on success.  A power loss
    or process kill mid-save leaves a ``.partial`` orphan instead
    of a corrupt half-image at the canonical path.  Readers (the
    sync collector, Drive uploader) only ever see complete files.
    """
    ext = os.path.splitext(source_path)[1].lower() or '.jpg'
    timestamp = int(time.time())
    filename = f"{prefix}_{entry_id}_{timestamp}{ext}"
    photos_dir = get_photos_dir()
    dest_path = os.path.join(photos_dir, filename)
    # Save to a sibling temp path; rename to final on success.
    tmp_path = os.path.join(
        photos_dir, f".tmp_{filename}.partial")

    # Try to resize using QPixmap (PySide6 is always available in this app)
    try:
        from PySide6.QtGui import QPixmap
        from PySide6.QtCore import Qt
        pixmap = QPixmap(source_path)
        if not pixmap.isNull():
            w, h = pixmap.width(), pixmap.height()
            if max(w, h) > MAX_DIMENSION:
                if w >= h:
                    pixmap = pixmap.scaledToWidth(
                        MAX_DIMENSION, Qt.SmoothTransformation)
                else:
                    pixmap = pixmap.scaledToHeight(
                        MAX_DIMENSION, Qt.SmoothTransformation)
                logger.info("Resized photo %dx%d -> %dx%d",
                            w, h, pixmap.width(), pixmap.height())

            # Save as JPEG for .jpg/.jpeg, otherwise keep original format.
            # Save to tmp path FIRST, then atomically rename.
            try:
                if ext in ('.jpg', '.jpeg'):
                    ok = pixmap.save(tmp_path, 'JPEG', JPEG_QUALITY)
                elif ext == '.png':
                    ok = pixmap.save(tmp_path, 'PNG')
                else:
                    ok = pixmap.save(tmp_path)
                if not ok:
                    # Save reported failure — clean up and fall
                    # through to shutil.copy2 fallback.
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                    raise OSError("QPixmap.save returned False")
                os.replace(tmp_path, dest_path)
            except Exception:
                # Clean up partial tmp on any failure path.
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except OSError:
                    pass
                raise

            relative = f"{PHOTOS_SUBDIR}/{filename}"
            logger.info("Stored photo: %s", relative)
            _register_source_hash(source_path, relative)
            return relative
    except Exception:
        logger.warning("QPixmap resize failed, falling back to file copy",
                       exc_info=True)

    # Fallback: plain file copy (no resize) — also atomic.
    try:
        shutil.copy2(source_path, tmp_path)
        os.replace(tmp_path, dest_path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise
    relative = f"{PHOTOS_SUBDIR}/{filename}"
    logger.info("Stored photo (unresized): %s", relative)
    _register_source_hash(source_path, relative)
    return relative


def _register_source_hash(source_path: str, relative_path: str) -> None:
    """Record the source file's content hash in the local registry.

    Hashes the *source* file (not the stored copy) because the UI
    duplicate check runs against the original file the user selects.
    Failures are logged but never propagated.
    """
    try:
        content_hash = compute_file_hash(source_path)
        from fam.models.photo_hash import store_local_photo_hash
        store_local_photo_hash(content_hash, relative_path)
    except Exception:
        logger.debug("Could not register photo hash for %s",
                     source_path, exc_info=True)


class UnsafePhotoPathError(ValueError):
    """Raised when a photo's stored relative_path tries to escape the
    photos/ directory tree (path-traversal / absolute path)."""


def _validate_relative_photo_path(relative_path: str) -> str:
    """Reject paths that try to escape the data directory.

    v2.0.3 fix (CRIT-SEC-2): pre-fix ``os.path.join(data_dir, abs)``
    returned ``abs`` verbatim when ``relative_path`` was absolute,
    so an attacker who could write to ``payment_line_items.photo_path``
    or ``fmnp_entries.photo_path`` (e.g. via the .fam import path or
    a tampered cloud-synced row) could turn the next Drive sync into
    an arbitrary-file exfiltration: setting
    ``photo_path = 'C:\\Users\\X\\.aws\\credentials'`` causes
    ``upload_pending_photos`` to upload that file to the volunteer's
    Drive folder.

    Reject:
      * absolute paths (``C:\\...``, ``/...``)
      * paths containing ``..`` segments
      * paths containing drive letters (``C:`` mid-string)
      * paths whose normalized form lands outside data_dir

    Returns the relative_path unchanged on acceptance.
    Raises ``UnsafePhotoPathError`` on rejection.
    """
    if not relative_path:
        return relative_path
    # Cheap up-front check
    if os.path.isabs(relative_path):
        raise UnsafePhotoPathError(
            f"absolute photo path rejected: {relative_path!r}")
    # Drive letter inside a string (Windows): "x:\\..."
    if len(relative_path) >= 2 and relative_path[1] == ':':
        raise UnsafePhotoPathError(
            f"drive-letter photo path rejected: {relative_path!r}")
    # Normalize and verify it stays within data_dir
    from fam.app import get_data_dir
    data_dir = os.path.realpath(get_data_dir())
    candidate = os.path.realpath(os.path.join(data_dir, relative_path))
    try:
        common = os.path.commonpath([data_dir, candidate])
    except ValueError:
        # Different drives on Windows
        raise UnsafePhotoPathError(
            f"photo path on different volume: {relative_path!r}")
    if common != data_dir:
        raise UnsafePhotoPathError(
            f"photo path escapes data_dir: {relative_path!r}")
    return relative_path


def get_photo_full_path(relative_path: str) -> str:
    """Convert a relative photo path to an absolute path.

    v2.0.3 (CRIT-SEC-2): the path is validated against the data_dir
    boundary so a malicious relative_path cannot escape into arbitrary
    file-system locations.  Raises UnsafePhotoPathError if rejected.
    """
    from fam.app import get_data_dir
    _validate_relative_photo_path(relative_path)
    return os.path.join(get_data_dir(), relative_path)


def photo_exists(relative_path: str) -> bool:
    """Check if a stored photo still exists on disk.

    Returns False on unsafe paths (rather than raising) because callers
    use this in display / dedup contexts where the safer behavior is
    "treat as missing" rather than crashing the UI.
    """
    if not relative_path:
        return False
    try:
        full = get_photo_full_path(relative_path)
    except UnsafePhotoPathError:
        logger.warning(
            "photo_exists: refusing unsafe path %r", relative_path)
        return False
    return os.path.isfile(full)


def compute_file_hash(file_path: str) -> str:
    """Compute SHA-256 hash of a file's contents.

    Reads in 64 KB chunks for memory efficiency on large images.
    Returns a lowercase hex digest string.
    """
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ── Multi-year retention: local photo cleanup after Drive upload ──
#
# v1.9.10 follow-up (2026-05-01): every uploaded photo lives both
# locally (the `<data_dir>/photos/` directory) AND in Google Drive
# (when sync is configured).  At year-1 scale the local directory
# is a few hundred MB and that's fine.  At year-5 it's tens of GB
# unless we prune.
#
# Cleanup contract:
#   * Only delete a local file when the corresponding DB row has a
#     non-empty ``photo_drive_url`` (Drive is the canonical store).
#   * Only delete files older than ``retention_days`` (default 90)
#     so the operator has a buffer for offline review.
#   * Never raise — failures are logged and the cleanup pass moves
#     on to the next file.
#
# Trigger: market close calls ``cleanup_uploaded_local_photos()``
# during the maintenance window (after the closing audit + sync).
# Operators can also disable via the ``photo_local_retention_days``
# setting (=0 disables cleanup; default 90 days).


def _drive_uploaded_paths() -> set[str]:
    """Return the set of relative photo paths whose corresponding
    DB row has a non-empty ``photo_drive_url``.

    Both ``payment_line_items.photo_path`` and
    ``fmnp_entries.photo_path`` may carry photos.  Each may be a
    JSON-encoded list of paths (multi-photo support) or a single
    path string.
    """
    from fam.utils.photo_paths import parse_photo_paths
    from fam.database.connection import get_connection
    conn = get_connection()
    paths: set[str] = set()
    for row in conn.execute(
            "SELECT photo_path FROM payment_line_items "
            "WHERE photo_drive_url IS NOT NULL "
            "AND photo_drive_url != ''").fetchall():
        for p in parse_photo_paths(row['photo_path']) or []:
            if p:
                paths.add(p)
    for row in conn.execute(
            "SELECT photo_path FROM fmnp_entries "
            "WHERE photo_drive_url IS NOT NULL "
            "AND photo_drive_url != ''").fetchall():
        for p in parse_photo_paths(row['photo_path']) or []:
            if p:
                paths.add(p)
    return paths


def cleanup_uploaded_local_photos(retention_days: int = 90,
                                    dry_run: bool = False) -> dict:
    """Delete local photos that are safely backed up to Google Drive.

    A file is eligible for deletion when ALL of these hold:

      1. Its relative path appears in a DB row whose
         ``photo_drive_url`` is non-empty (Drive has it).
      2. The file's mtime is older than ``retention_days`` days
         (operator buffer for offline review of recent images).
      3. The file is in the local photos directory.

    Returns a dict with counts of what was scanned, eligible,
    deleted, and skipped.  When ``dry_run=True`` the scan happens
    but nothing is removed — useful for preview and tests.

    Never raises.  All errors are logged.
    """
    out = {
        'scanned': 0, 'eligible': 0, 'deleted': 0,
        'skipped_recent': 0, 'skipped_no_drive': 0, 'errors': 0,
    }
    if retention_days <= 0:
        logger.info(
            "Local photo cleanup disabled (retention_days=%d)",
            retention_days)
        return out
    try:
        photos_dir = get_photos_dir()
    except Exception:
        logger.exception("Could not resolve photos directory — "
                         "skipping cleanup")
        return out

    cutoff = time.time() - (retention_days * 86400)
    try:
        uploaded = _drive_uploaded_paths()
    except Exception:
        logger.exception(
            "Could not enumerate Drive-backed photos — skipping "
            "cleanup pass")
        return out

    try:
        names = os.listdir(photos_dir)
    except OSError:
        logger.exception(
            "Could not list photos directory %s", photos_dir)
        return out

    for name in names:
        out['scanned'] += 1
        full = os.path.join(photos_dir, name)
        rel = f"{PHOTOS_SUBDIR}/{name}"
        if rel not in uploaded:
            out['skipped_no_drive'] += 1
            continue
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            out['errors'] += 1
            continue
        if mtime > cutoff:
            out['skipped_recent'] += 1
            continue
        out['eligible'] += 1
        if dry_run:
            continue
        try:
            os.remove(full)
            out['deleted'] += 1
            # v2.0.1: also remove the corresponding ``local_photo_hashes``
            # row so future hash lookups don't return a path to a file
            # that no longer exists on disk.  (The ``photo_hashes``
            # table — Drive cache — is left intact; Drive still has
            # the file.)
            try:
                from fam.database.connection import get_connection
                conn = get_connection()
                conn.execute(
                    "DELETE FROM local_photo_hashes WHERE relative_path=?",
                    (rel,),
                )
                conn.commit()
            except Exception:
                logger.debug(
                    "Could not delete local_photo_hashes row for %s",
                    rel, exc_info=True)
        except OSError:
            logger.warning("Could not delete %s", full, exc_info=True)
            out['errors'] += 1

    logger.info(
        "Local photo cleanup: scanned=%d eligible=%d deleted=%d "
        "skipped_recent=%d skipped_no_drive=%d errors=%d "
        "(dry_run=%s, retention=%dd)",
        out['scanned'], out['eligible'], out['deleted'],
        out['skipped_recent'], out['skipped_no_drive'],
        out['errors'], dry_run, retention_days)
    return out
