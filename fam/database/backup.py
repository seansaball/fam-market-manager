"""SQLite database backup with retention management."""

import logging
import os
import re
import sqlite3
from fam.database.connection import get_db_path
from fam.utils.timezone import eastern_now

logger = logging.getLogger(__name__)

# Keep this many most-recent backup files PER MARKET CODE.
# v2.0.3 fix (HIGH-2): pre-fix the retention sweep sorted across
# every market and trimmed to the newest 20 globally.  A laptop
# running Market A (twice weekly) and Market B (monthly) would
# evict Market B's monthly backups within ~10 weeks of Market A
# activity even though they were the only forensic copy of Market B.
# Now retention is per-market-code: the global cap is
# MAX_MARKET_CODES * BACKUP_RETENTION_COUNT_PER_MARKET, but no
# single market can starve another's history.
BACKUP_RETENTION_COUNT_PER_MARKET = 20
# Legacy alias for backward-compat with any caller importing this
BACKUP_RETENTION_COUNT = BACKUP_RETENTION_COUNT_PER_MARKET

# Backup subdirectory name (created under the data directory)
BACKUP_DIR_NAME = "backups"

# v2.0.3 fix (HIGH-5): match the timestamp segment of either the
# new microsecond-resolution filename OR the legacy second-resolution
# filename so retention can sort both without surprises.
#   New: fam_{CODE}_backup_YYYYMMDD_HHMMSS_NNNNNN_{reason}.db
#   Old: fam_{CODE}_backup_YYYYMMDD_HHMMSS_{reason}.db
#   Old (no code): fam_backup_YYYYMMDD_HHMMSS_{reason}.db
_BACKUP_FILENAME_RE = re.compile(
    r'^fam_(?P<code>[A-Za-z0-9]+)?_?backup_'
    r'(?P<ts>\d{8}_\d{6}(?:_\d{6})?)_'
    r'(?P<reason>[A-Za-z_]+)\.db$'
)


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
    # v2.0.3 fix (HIGH-5): include microseconds in the filename so two
    # backups landing in the same wall-clock second don't silently
    # collide (the second ``source.backup(dest)`` would otherwise
    # overwrite the first via ``sqlite3.connect`` on the same path).
    now = eastern_now()
    timestamp = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond:06d}"
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
    """Delete oldest backup files beyond the per-market retention count.

    v2.0.3 fix (HIGH-2): bucket by market_code so a high-volume market
    can't starve a low-volume one.  Sort by the timestamp segment
    (lexicographic on YYYYMMDD_HHMMSS[_uuuuuu]) within each bucket
    and retain the newest ``BACKUP_RETENTION_COUNT_PER_MARKET`` per
    code.  Files that don't match either filename pattern (legacy
    or current) are left alone — caller may have manually placed
    them in the directory.
    """
    try:
        buckets: dict[str, list[tuple[str, str]]] = {}
        for filename in os.listdir(backup_dir):
            if not (filename.startswith('fam_') and filename.endswith('.db')):
                continue
            m = _BACKUP_FILENAME_RE.match(filename)
            if m is None:
                continue
            code = m.group('code') or ''  # '' bucket = legacy no-code backups
            ts = m.group('ts')
            buckets.setdefault(code, []).append((ts, filename))

        for code, items in buckets.items():
            # Sort by timestamp ascending — oldest first
            items.sort(key=lambda t: t[0])
            if len(items) > BACKUP_RETENTION_COUNT_PER_MARKET:
                cull_count = len(items) - BACKUP_RETENTION_COUNT_PER_MARKET
                for _ts, filename in items[:cull_count]:
                    filepath = os.path.join(backup_dir, filename)
                    try:
                        os.remove(filepath)
                        logger.info(
                            "Old backup removed (market=%r): %s",
                            code or '<no-code>', filename)
                    except OSError:
                        logger.warning(
                            "Could not remove old backup: %s", filename)
    except Exception:
        logger.exception("Error during backup retention cleanup")
