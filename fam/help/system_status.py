"""Live diagnostic snapshot for the Help → System Status tab.

Pure data collection — no UI.  The Help screen renders the dict
returned by :func:`collect_status` and exposes a "Copy Diagnostic
Info" button that serializes it as plain text for the volunteer to
paste into an email or chat.

This intentionally avoids importing any UI module so it can be
exercised from tests without a QApplication.
"""

import os
from typing import Any


def _get_data_dir_size(data_dir: str) -> dict[str, int]:
    """Return sizes (in bytes) of key sub-paths inside the data dir.

    Missing paths are reported as 0 — never raises.
    """
    sizes = {
        'database': 0,
        'photos_total': 0,
        'photos_count': 0,
        'backups_total': 0,
        'backups_count': 0,
        'log': 0,
        'ledger_backup': 0,
    }

    db_path = os.path.join(data_dir, 'fam_data.db')
    if os.path.isfile(db_path):
        sizes['database'] = os.path.getsize(db_path)

    log_path = os.path.join(data_dir, 'fam_manager.log')
    if os.path.isfile(log_path):
        sizes['log'] = os.path.getsize(log_path)

    ledger_path = os.path.join(data_dir, 'fam_ledger_backup.txt')
    if os.path.isfile(ledger_path):
        sizes['ledger_backup'] = os.path.getsize(ledger_path)

    photos_dir = os.path.join(data_dir, 'photos')
    if os.path.isdir(photos_dir):
        for f in os.listdir(photos_dir):
            full = os.path.join(photos_dir, f)
            if os.path.isfile(full):
                sizes['photos_total'] += os.path.getsize(full)
                sizes['photos_count'] += 1

    backups_dir = os.path.join(data_dir, 'backups')
    if os.path.isdir(backups_dir):
        for f in os.listdir(backups_dir):
            full = os.path.join(backups_dir, f)
            if os.path.isfile(full) and f.endswith('.db'):
                sizes['backups_total'] += os.path.getsize(full)
                sizes['backups_count'] += 1

    return sizes


def _safe_get_setting(key: str, default: Any = None) -> Any:
    """Wrapper that never raises — falls back to default on any error."""
    try:
        from fam.utils.app_settings import get_setting
        return get_setting(key, default)
    except Exception:
        return default


def _safe_count(query: str) -> int:
    """Run a COUNT query, returning 0 on any error."""
    try:
        from fam.database.connection import get_connection
        conn = get_connection()
        row = conn.execute(query).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _safe_open_market_day_label() -> str:
    """Return a short human-readable description of the open market day,
    or 'None' if no market day is open.  Never raises."""
    try:
        from fam.models.market_day import get_open_market_day
        md = get_open_market_day()
        if md:
            return f"{md.get('market_name', '?')} — {md.get('date', '?')}"
        return 'None'
    except Exception:
        return 'unknown'


def _safe_oldest_backup(backups_dir: str) -> str:
    """Return the filename of the oldest backup, or empty string."""
    try:
        if not os.path.isdir(backups_dir):
            return ''
        files = sorted(
            f for f in os.listdir(backups_dir)
            if f.endswith('.db') and f.startswith('fam_')
        )
        return files[0] if files else ''
    except Exception:
        return ''


def collect_status() -> dict[str, Any]:
    """Snapshot of every diagnostic data point the System Status tab shows.

    Returns a dict with stable keys.  Values are pre-formatted strings
    or numbers as appropriate for direct display.  All errors are
    swallowed — the caller can rely on this not raising under any
    condition.
    """
    from fam import __version__

    # Resolve data dir without crashing if frozen-mode setup hasn't run
    try:
        from fam.app import get_data_dir
        data_dir = get_data_dir()
    except Exception:
        data_dir = ''

    sizes = _get_data_dir_size(data_dir) if data_dir else {
        'database': 0, 'photos_total': 0, 'photos_count': 0,
        'backups_total': 0, 'backups_count': 0, 'log': 0,
        'ledger_backup': 0,
    }

    # Counts (best-effort)
    confirmed_txns = _safe_count(
        "SELECT COUNT(*) FROM transactions WHERE status IN ('Confirmed', 'Adjusted')"
    )
    voided_txns = _safe_count(
        "SELECT COUNT(*) FROM transactions WHERE status = 'Voided'"
    )
    fmnp_entries = _safe_count(
        "SELECT COUNT(*) FROM fmnp_entries WHERE status = 'Active'"
    )
    market_days = _safe_count("SELECT COUNT(*) FROM market_days")
    audit_rows = _safe_count("SELECT COUNT(*) FROM audit_log")

    return {
        'app_version': __version__,
        'data_dir': data_dir,

        # Sync state
        'last_sync_at': _safe_get_setting('last_sync_at', '(never)') or '(never)',
        'last_sync_error': _safe_get_setting('last_sync_error', '') or '',
        'sync_spreadsheet_id': _safe_get_setting('sync_spreadsheet_id', '') or '',
        'sync_credentials_loaded':
            _safe_get_setting('sync_credentials_loaded', '0') == '1',
        'drive_folder_id':
            _safe_get_setting('drive_photos_folder_id', '') or '',

        # Update state
        'last_update_check':
            _safe_get_setting('update_last_check', '(never)') or '(never)',
        'update_repo_url':
            _safe_get_setting(
                'update_repo_url',
                'https://github.com/seansaball/fam-market-manager') or '',

        # Market state
        'market_code': _safe_get_setting('market_code', '(not set)') or '(not set)',
        'device_id': _safe_get_setting('device_id', '(not set)') or '(not set)',
        'open_market_day': _safe_open_market_day_label(),

        # Counts
        'confirmed_transactions': confirmed_txns,
        'voided_transactions': voided_txns,
        'fmnp_entries_active': fmnp_entries,
        'market_days_total': market_days,
        'audit_log_rows': audit_rows,

        # Disk usage
        'database_bytes': sizes['database'],
        'photos_total_bytes': sizes['photos_total'],
        'photos_count': sizes['photos_count'],
        'backups_total_bytes': sizes['backups_total'],
        'backups_count': sizes['backups_count'],
        'oldest_backup':
            _safe_oldest_backup(os.path.join(data_dir, 'backups')) if data_dir else '',
        'log_bytes': sizes['log'],
        'ledger_backup_bytes': sizes['ledger_backup'],
    }


def _human_bytes(n: int) -> str:
    """Format byte count as KB / MB / GB."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024 / 1024 / 1024:.2f} GB"


def format_status_for_clipboard(status: dict[str, Any]) -> str:
    """Format the status dict as a plain-text block suitable for pasting
    into an email or chat.  Stable layout for diff-friendly support."""
    lines = [
        "FAM Market Manager — System Status",
        "═" * 50,
        f"App version       : {status['app_version']}",
        f"Data directory    : {status['data_dir']}",
        f"Market code       : {status['market_code']}",
        f"Device ID         : {status['device_id']}",
        f"Open market day   : {status['open_market_day']}",
        "",
        "── Sync ──────────────────────────────────────",
        f"Last sync         : {status['last_sync_at']}",
        f"Last sync error   : {status['last_sync_error'] or '(none)'}",
        f"Sheet configured  : "
        f"{'yes' if status['sync_spreadsheet_id'] else 'no'}",
        f"Credentials loaded: "
        f"{'yes' if status['sync_credentials_loaded'] else 'no'}",
        f"Drive folder id   : {status['drive_folder_id'] or '(not set)'}",
        "",
        "── Updates ───────────────────────────────────",
        f"Last update check : {status['last_update_check']}",
        f"Update source     : {status['update_repo_url']}",
        "",
        "── Records ───────────────────────────────────",
        f"Confirmed txns    : {status['confirmed_transactions']:,}",
        f"Voided txns       : {status['voided_transactions']:,}",
        f"Active FMNP rows  : {status['fmnp_entries_active']:,}",
        f"Market days total : {status['market_days_total']:,}",
        f"Audit log rows    : {status['audit_log_rows']:,}",
        "",
        "── Disk usage ────────────────────────────────",
        f"Database          : {_human_bytes(status['database_bytes'])}",
        f"Photos folder     : "
        f"{_human_bytes(status['photos_total_bytes'])}"
        f" ({status['photos_count']:,} files)",
        f"Backups folder    : "
        f"{_human_bytes(status['backups_total_bytes'])}"
        f" ({status['backups_count']:,} files)",
        f"Oldest backup     : {status['oldest_backup'] or '(none)'}",
        f"Application log   : {_human_bytes(status['log_bytes'])}",
        f"Ledger backup     : {_human_bytes(status['ledger_backup_bytes'])}",
    ]
    return '\n'.join(lines)
