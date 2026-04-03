"""SQLite database backup with retention management."""

import logging
import os
import sqlite3
from fam.database.connection import get_db_path
from fam.utils.timezone import eastern_now

logger = logging.getLogger(__name__)

# Keep this many most-recent backup files
BACKUP_RETENTION_COUNT = 20

# Backup subdirectory name (created under the data directory)
BACKUP_DIR_NAME = "backups"


def get_backup_dir() -> str:
    """Return the backup directory path, creating it if needed."""
    db_path = get_db_path()
    data_dir = os.path.dirname(os.path.abspath(db_path))
    backup_dir = os.path.join(data_dir, BACKUP_DIR_NAME)
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir


def create_backup(reason: str = "manual") -> str | None:
    """Create a backup of the database using SQLite's backup API.

    Parameters
    ----------
    reason : str
        Label for the backup type: "market_open", "market_close", "auto".
        Included in the filename for easy identification.

    Returns
    -------
    str or None
        Path to the backup file, or None if the backup failed.

    This function never raises -- all errors are logged silently
    so it cannot interfere with the normal workflow.
    """
    try:
        return _create_backup_inner(reason)
    except Exception:
        logger.exception("Failed to create database backup (reason=%s)", reason)
        return None


def _create_backup_inner(reason: str) -> str | None:
    """Core backup logic (may raise)."""
    db_path = get_db_path()
    if not os.path.exists(db_path):
        logger.warning("Database file does not exist: %s", db_path)
        return None

    backup_dir = get_backup_dir()
    timestamp = eastern_now().strftime("%Y%m%d_%H%M%S")
    from fam.utils.app_settings import get_market_code
    code = get_market_code()
    if code:
        filename = f"fam_{code}_backup_{timestamp}_{reason}.db"
    else:
        filename = f"fam_backup_{timestamp}_{reason}.db"
    backup_path = os.path.join(backup_dir, filename)

    # Use SQLite backup API for a consistent hot copy
    # (handles WAL mode properly, unlike shutil.copy2)
    source = sqlite3.connect(db_path)
    source.execute("PRAGMA journal_mode=WAL")
    try:
        dest = sqlite3.connect(backup_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()

    logger.info("Database backup created: %s", backup_path)

    # Clean up old backups beyond retention limit
    _enforce_retention(backup_dir)

    return backup_path


def _enforce_retention(backup_dir: str):
    """Delete oldest backup files beyond the retention count."""
    try:
        # List only our backup files (lexicographic sort = chronological
        # because filenames use YYYYMMDD_HHMMSS timestamps)
        backups = sorted(
            f for f in os.listdir(backup_dir)
            if f.startswith("fam_") and f.endswith(".db") and "backup_" in f
        )
        if len(backups) > BACKUP_RETENTION_COUNT:
            to_delete = backups[:len(backups) - BACKUP_RETENTION_COUNT]
            for filename in to_delete:
                filepath = os.path.join(backup_dir, filename)
                try:
                    os.remove(filepath)
                    logger.info("Old backup removed: %s", filename)
                except OSError:
                    logger.warning("Could not remove old backup: %s", filename)
    except Exception:
        logger.exception("Error during backup retention cleanup")
