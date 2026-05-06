"""Live diagnostic snapshot for the Help → System Status tab.

Pure data collection — no UI.  The Help screen renders the dict
returned by :func:`collect_status` and exposes a "Copy Diagnostic
Info" button that serializes it as plain text for the volunteer to
paste into an email or chat.

This intentionally avoids importing any UI module so it can be
exercised from tests without a QApplication.

v1.9.10 follow-up (2026-05-01) — diagnostic block now includes:

  * **Instance-lock state** — whether ``.fam_instance.lock`` exists
    and the holding PID, so a coordinator triaging an "Already
    running" report can tell at a glance whether it's a stuck lock
    or a real second copy.
  * **Pending-update marker** — whether ``_pending_update.json``
    is still on disk (i.e. an in-flight update did not complete).
  * **Rewards state** — whether rewards are enabled and how many
    rules are active, so a Generated-Rewards-empty report can be
    triaged without screenshare.
  * **Log tail** — last 30 non-empty lines of ``fam_manager.log``,
    appended to the clipboard text so a coordinator gets the most
    recent error context without a separate file attach.
"""

import json
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
    """Run a COUNT query, returning 0 on any error.

    v2.0.1: distinguish ``OperationalError`` (schema/query bug —
    likely a code mistake worth surfacing in the diagnostic) from
    other failures (a locked DB, a transient I/O error, etc.).
    Operational errors return ``-1`` as a sentinel so the
    clipboard text renders ``(error)`` rather than a misleading
    ``0``.  Generic failures still return 0 to keep the
    diagnostic robust during routine contention.
    """
    try:
        import sqlite3
        from fam.database.connection import get_connection
        conn = get_connection()
        row = conn.execute(query).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        # Schema mismatch / query bug — surface as sentinel so the
        # diagnostic doesn't silently lie about the row count.
        import logging as _log
        _log.getLogger(__name__).warning(
            "OperationalError running diagnostic count query",
            exc_info=True)
        return -1
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


def _safe_instance_lock_state(data_dir: str) -> dict[str, Any]:
    """Inspect ``.fam_instance.lock`` if present.

    Returns a dict with keys ``exists``, ``pid`` (int or None),
    ``pid_running`` (bool or None — None means we couldn't check).
    Never raises.

    Useful for triaging "Already running" reports — a coordinator
    can tell whether the lock is held by a real process or a
    stale leftover from a crash.
    """
    state: dict[str, Any] = {
        'exists': False, 'pid': None, 'pid_running': None,
    }
    try:
        from fam.database.instance_lock import LOCK_FILENAME
        lock_path = os.path.join(data_dir, LOCK_FILENAME)
        if not os.path.isfile(lock_path):
            return state
        state['exists'] = True
        try:
            with open(lock_path, 'r', encoding='utf-8') as f:
                # PID is written at offset 1 (past the locked byte 0)
                f.seek(1)
                content = f.read().strip()
            if content:
                state['pid'] = int(content.split('\n', 1)[0])
        except (OSError, ValueError):
            pass
        # Best-effort liveness check via the Windows OpenProcess API.
        #
        # v2.0.1 fix: previously we shelled out to ``tasklist /FI ...``
        # — which (a) flashed a console window every time the System
        # Status tab was opened or refreshed, (b) added 1-2s of UI lag
        # while AV inspected ``tasklist.exe``, and (c) made the app
        # feel sketchy.  Direct ctypes call is silent, sub-millisecond,
        # and doesn't trigger AV.
        #
        # ``PROCESS_QUERY_LIMITED_INFORMATION`` (0x1000) is the
        # lowest-privilege flag that works against any process
        # regardless of owner / token — designed for exactly this
        # "is this PID alive?" check.  Returns NULL on dead PIDs.
        pid = state['pid']
        if pid:
            try:
                import sys
                if sys.platform == 'win32':
                    import ctypes
                    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(
                        PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                    if handle:
                        kernel32.CloseHandle(handle)
                        state['pid_running'] = True
                    else:
                        state['pid_running'] = False
            except Exception:
                pass
    except Exception:
        pass
    return state


def _safe_pending_update_state(data_dir: str) -> dict[str, Any]:
    """Inspect ``_pending_update.json`` if present.

    Returns ``{'exists': bool, 'target_version': str or None}``.
    A present marker means an update was started and the app
    relaunched; if the version doesn't match, the user has
    already seen (or will see) the "Update did not complete"
    dialog.
    """
    state: dict[str, Any] = {'exists': False, 'target_version': None}
    try:
        from fam.update.checker import PENDING_UPDATE_FILENAME
        marker_path = os.path.join(data_dir, PENDING_UPDATE_FILENAME)
        if not os.path.isfile(marker_path):
            return state
        state['exists'] = True
        try:
            with open(marker_path, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            state['target_version'] = str(
                payload.get('target_version', '')) or None
        except (OSError, json.JSONDecodeError):
            pass
    except Exception:
        pass
    return state


def _safe_rewards_state() -> dict[str, Any]:
    """Return rewards configuration summary.

    Returns ``{'enabled': bool, 'active_rules': int,
    'rewards_today': int}``.  All best-effort.
    """
    state: dict[str, Any] = {
        'enabled': False, 'active_rules': 0, 'rewards_today': 0,
    }
    try:
        # Rewards-enabled is stored as a setting; default off.
        flag = _safe_get_setting('rewards_enabled', '0')
        state['enabled'] = str(flag) == '1'
    except Exception:
        pass
    state['active_rules'] = _safe_count(
        "SELECT COUNT(*) FROM reward_rules WHERE is_active = 1"
    )
    state['rewards_today'] = _safe_count(
        "SELECT COUNT(*) FROM generated_rewards "
        "WHERE date(generated_at) = date('now', 'localtime')"
    )
    return state


def _safe_auto_check_state() -> dict:
    """Summarize the auto-update-check state for the diagnostic.

    Returns ``{enabled, last_check, latest_known_remote, dismissed_version,
    snoozed_until, eligible_now}`` so a coordinator triaging "the popup
    never fires" can see exactly which gate is closed.

    Never raises.
    """
    state = {
        'enabled': True,
        'last_check': '(never)',
        'latest_known_remote': '',
        'dismissed_version': '',
        'snoozed_until': '',
        'eligible_now': True,
    }
    try:
        from fam.utils.app_settings import (
            is_auto_update_check_enabled, get_last_update_check, get_setting,
        )
        state['enabled'] = bool(is_auto_update_check_enabled())
        state['last_check'] = (
            get_last_update_check() or '(never)')
        state['latest_known_remote'] = get_setting(
            'update_last_version', '') or ''
        state['dismissed_version'] = get_setting(
            'update_dismissed_version', '') or ''
        state['snoozed_until'] = get_setting(
            'update_remind_after', '') or ''
        # Compute eligibility for the next check (does NOT include
        # cache-replay path, which has its own gates).
        from datetime import datetime as _dt, timedelta
        from fam.utils.timezone import eastern_now, EASTERN
        last_str = state['last_check']
        if last_str and last_str != '(never)':
            try:
                last_dt = _dt.fromisoformat(last_str)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=EASTERN)
                # Mirrors MainWindow._AUTO_CHECK_COOLDOWN_HOURS;
                # duplicated here so this module stays UI-thread
                # independent.
                state['eligible_now'] = (
                    eastern_now() - last_dt >= timedelta(hours=6))
            except (ValueError, TypeError):
                state['eligible_now'] = True
    except Exception:
        pass
    return state


def _safe_log_tail(data_dir: str, max_lines: int = 30) -> list[str]:
    """Return the last *max_lines* non-empty lines of fam_manager.log.

    Uses a simple full-read because the log file rolls at 5 MB by
    default; well under any memory concern.  Never raises.
    """
    try:
        log_path = os.path.join(data_dir, 'fam_manager.log')
        if not os.path.isfile(log_path):
            return []
        # Cap read at 256 KB even if rolling failed — bound the
        # diagnostic blob size on a misbehaving log.
        size = os.path.getsize(log_path)
        with open(log_path, 'rb') as f:
            if size > 262144:
                f.seek(size - 262144)
            data = f.read()
        text = data.decode('utf-8', errors='replace')
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        return lines[-max_lines:]
    except Exception:
        return []


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

    # v1.9.10 additions — instance lock, pending update, rewards, log tail
    instance_lock = _safe_instance_lock_state(data_dir) if data_dir else {
        'exists': False, 'pid': None, 'pid_running': None,
    }
    pending_update = _safe_pending_update_state(data_dir) if data_dir else {
        'exists': False, 'target_version': None,
    }
    rewards = _safe_rewards_state()
    log_tail = _safe_log_tail(data_dir) if data_dir else []
    auto_check = _safe_auto_check_state()

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

        # v1.9.10 — diagnostic-quality fields for the no-onsite-support
        # scenario.  All best-effort, never raise.
        'instance_lock_exists': instance_lock['exists'],
        'instance_lock_pid': instance_lock['pid'],
        'instance_lock_pid_running': instance_lock['pid_running'],
        'pending_update_exists': pending_update['exists'],
        'pending_update_target': pending_update['target_version'],
        'rewards_enabled': rewards['enabled'],
        'rewards_active_rules': rewards['active_rules'],
        'rewards_generated_today': rewards['rewards_today'],
        'log_tail': log_tail,

        # v2.0.1 — auto-update-check visibility for "popup never
        # fires" triage.  Coordinators can read these to determine
        # whether the check is disabled, recently ran, snoozed, or
        # blocked by a permanent dismiss.
        'auto_check_enabled': auto_check['enabled'],
        'auto_check_last': auto_check['last_check'],
        'auto_check_eligible_now': auto_check['eligible_now'],
        'auto_check_latest_known_remote': auto_check['latest_known_remote'],
        'auto_check_dismissed_version': auto_check['dismissed_version'],
        'auto_check_snoozed_until': auto_check['snoozed_until'],
    }


def _fmt_count(n: int) -> str:
    """Render a diagnostic row count.  ``-1`` (the OperationalError
    sentinel from :func:`_safe_count`) renders as ``(error)`` so a
    coordinator reading the clipboard text doesn't mistake a query
    bug for a healthy zero."""
    if n == -1:
        return "(error)"
    return f"{n:,}"


def _human_bytes(n: int) -> str:
    """Format byte count as KB / MB / GB."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024 / 1024 / 1024:.2f} GB"


def _mask_id(value: str, keep_prefix: int = 4, keep_suffix: int = 4) -> str:
    """Partially mask an opaque ID for paste-into-chat safety.

    v2.0.3 fix (HIGH-SEC-2): pre-fix the diagnostic clipboard exposed
    full ``sync_spreadsheet_id`` and ``drive_folder_id`` values.
    Pasted into a chat run by an attacker, these enable targeted
    social-engineering / auth-token attacks.  Mask the middle while
    keeping enough characters for the user to confirm "yes that's
    my sheet" when troubleshooting.

    Empty string stays empty.  Short values pass through (already
    too short to mask meaningfully).
    """
    if not value:
        return ''
    if len(value) <= keep_prefix + keep_suffix + 4:
        return value
    return f"{value[:keep_prefix]}…{value[-keep_suffix:]}"


def format_status_for_clipboard(status: dict[str, Any]) -> str:
    """Format the status dict as a plain-text block suitable for pasting
    into an email or chat.  Stable layout for diff-friendly support.

    Uses ASCII-only divider characters so it pastes cleanly into Outlook
    2016 / older mail clients on managed laptops.
    """
    # ── Pending-update line (only if a marker exists) ──
    pending_lines: list[str] = []
    if status.get('pending_update_exists'):
        target = status.get('pending_update_target') or '(unknown)'
        pending_lines = [
            f"Pending update    : YES — target was {target}",
            "                    (the 'Update did not complete' "
            "dialog will fire on next clean launch)",
        ]

    # ── Instance lock summary ──
    lock_summary = "no lock file"
    if status.get('instance_lock_exists'):
        pid = status.get('instance_lock_pid') or '?'
        running = status.get('instance_lock_pid_running')
        if running is True:
            lock_summary = f"held by pid {pid} (running)"
        elif running is False:
            lock_summary = f"STALE — pid {pid} no longer running"
        else:
            lock_summary = f"held — pid {pid} (liveness unknown)"

    rewards_summary = (
        f"enabled, {status.get('rewards_active_rules', 0)} active rule(s), "
        f"{status.get('rewards_generated_today', 0)} earned today"
        if status.get('rewards_enabled')
        else "disabled"
    )

    lines = [
        "FAM Market Manager -- System Status",
        "=" * 50,
        f"App version       : {status['app_version']}",
        f"Data directory    : {status['data_dir']}",
        f"Market code       : {status['market_code']}",
        f"Device ID         : {status['device_id']}",
        f"Open market day   : {status['open_market_day']}",
        f"Instance lock     : {lock_summary}",
        "",
        "-- Sync ------------------------------------------",
        f"Last sync         : {status['last_sync_at']}",
        f"Last sync error   : {status['last_sync_error'] or '(none)'}",
        f"Sheet configured  : "
        f"{'yes' if status['sync_spreadsheet_id'] else 'no'}",
        f"Sheet id (masked) : "
        f"{_mask_id(status['sync_spreadsheet_id']) or '(not set)'}",
        f"Credentials loaded: "
        f"{'yes' if status['sync_credentials_loaded'] else 'no'}",
        f"Drive folder id   : "
        f"{_mask_id(status['drive_folder_id']) or '(not set)'}",
        "",
        "-- Updates ---------------------------------------",
        f"Last update check : {status['last_update_check']}",
        f"Update source     : {status['update_repo_url']}",
        # v2.0.1: surface the auto-check state so a coordinator
        # triaging "the popup never fires" can see exactly which
        # gate is closed (disabled / cooldown / dismissed / snoozed).
        f"Auto-check        : "
        f"{'enabled' if status.get('auto_check_enabled') else 'DISABLED'}",
        f"Cooldown          : "
        f"{'eligible to run' if status.get('auto_check_eligible_now') else 'within 6h cooldown'}",
        f"Latest known      : "
        f"{status.get('auto_check_latest_known_remote') or '(none cached)'}",
        f"Dismissed version : "
        f"{status.get('auto_check_dismissed_version') or '(none)'}",
        f"Snoozed until     : "
        f"{status.get('auto_check_snoozed_until') or '(not snoozed)'}",
    ]
    lines.extend(pending_lines)
    lines.extend([
        "",
        "-- Rewards ---------------------------------------",
        f"Rewards           : {rewards_summary}",
        "",
        "-- Records ---------------------------------------",
        f"Confirmed txns    : {_fmt_count(status['confirmed_transactions'])}",
        f"Voided txns       : {_fmt_count(status['voided_transactions'])}",
        f"Active FMNP rows  : {_fmt_count(status['fmnp_entries_active'])}",
        f"Market days total : {_fmt_count(status['market_days_total'])}",
        f"Audit log rows    : {_fmt_count(status['audit_log_rows'])}",
        "",
        "-- Disk usage ------------------------------------",
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
    ])

    # ── Log tail — last 30 lines of fam_manager.log ──
    log_tail = status.get('log_tail') or []
    if log_tail:
        lines.extend([
            "",
            "-- Log tail (last %d lines of fam_manager.log) ---"
            % len(log_tail),
        ])
        lines.extend(log_tail)

    return '\n'.join(lines)
