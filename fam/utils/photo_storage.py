"""Local photo storage — copy and optionally resize check/receipt photos."""

import hashlib
import logging
import os
import shutil
import time

logger = logging.getLogger('fam.utils.photo_storage')

MAX_DIMENSION = 1920  # Longest side, pixels
JPEG_QUALITY = 85
PHOTOS_SUBDIR = 'photos'


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
    """
    ext = os.path.splitext(source_path)[1].lower() or '.jpg'
    timestamp = int(time.time())
    filename = f"{prefix}_{entry_id}_{timestamp}{ext}"
    dest_path = os.path.join(get_photos_dir(), filename)

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

            # Save as JPEG for .jpg/.jpeg, otherwise keep original format
            if ext in ('.jpg', '.jpeg'):
                pixmap.save(dest_path, 'JPEG', JPEG_QUALITY)
            elif ext == '.png':
                pixmap.save(dest_path, 'PNG')
            else:
                pixmap.save(dest_path)

            relative = f"{PHOTOS_SUBDIR}/{filename}"
            logger.info("Stored photo: %s", relative)
            _register_source_hash(source_path, relative)
            return relative
    except Exception:
        logger.warning("QPixmap resize failed, falling back to file copy",
                       exc_info=True)

    # Fallback: plain file copy (no resize)
    shutil.copy2(source_path, dest_path)
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


def get_photo_full_path(relative_path: str) -> str:
    """Convert a relative photo path to an absolute path."""
    from fam.app import get_data_dir
    return os.path.join(get_data_dir(), relative_path)


def photo_exists(relative_path: str) -> bool:
    """Check if a stored photo still exists on disk."""
    if not relative_path:
        return False
    full = get_photo_full_path(relative_path)
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
