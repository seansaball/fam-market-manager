"""Centralized helpers for the app_settings key-value table."""

import logging

from fam.database.connection import get_connection

logger = logging.getLogger('fam.utils.app_settings')

# ── Default values ────────────────────────────────────────────
DEFAULT_LARGE_RECEIPT_THRESHOLD = 100.00
DEFAULT_REPO_URL = "https://github.com/seansaball/fam-market-manager"


# ── Generic helpers ───────────────────────────────────────────

def get_setting(key: str, default: str | None = None) -> str | None:
    """Read a single value from app_settings. Returns *default* if missing."""
    try:
        row = get_connection().execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else default
    except Exception:
        return default


def set_setting(key: str, value: str) -> None:
    """Write (insert or update) a single value in app_settings."""
    try:
        conn = get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()
    except Exception:
        logger.warning("Could not save setting %s", key, exc_info=True)


# ── Large receipt threshold ───────────────────────────────────

def get_large_receipt_threshold() -> float:
    """Return the threshold above which a receipt triggers a warning dialog."""
    raw = get_setting('large_receipt_threshold')
    if raw is not None:
        try:
            return float(raw)
        except (ValueError, TypeError):
            pass
    return DEFAULT_LARGE_RECEIPT_THRESHOLD


def set_large_receipt_threshold(value: float) -> None:
    """Persist the large-receipt warning threshold."""
    set_setting('large_receipt_threshold', str(value))


# ── Market code (auto-derived from market name) ──────────────

def get_market_code() -> str | None:
    """Return the current market code, or None if no market day has been opened."""
    return get_setting('market_code')


def set_market_code(code: str) -> None:
    """Store a market code string. Accepts 1-4 uppercase alpha chars."""
    code = code.strip().upper()
    if not code or not code.isalpha() or len(code) > 4:
        raise ValueError("Market code must be 1-4 letters")
    set_setting('market_code', code)


def derive_market_code(market_name: str) -> str:
    """Derive a short uppercase code from a market name.

    Rules:
      - Take the first letter of each word  (e.g. "Downtown Market" → "DM")
      - If only one word, take first 2 chars (e.g. "Riverside" → "RI")
      - Clamp to 4 chars max
      - Fall back to first 2 chars if no alpha letters found
    """
    words = market_name.split()
    if len(words) >= 2:
        initials = ''.join(w[0] for w in words if w and w[0].isalpha())
        code = initials[:4].upper()
    else:
        # Single word — take first 2 alpha chars
        alpha = ''.join(c for c in market_name if c.isalpha())
        code = alpha[:2].upper()

    return code if code else "MK"


def update_market_code_from_name(market_name: str) -> str:
    """Derive and persist the market code from a market name. Returns the code."""
    code = derive_market_code(market_name)
    set_market_code(code)
    logger.info("Market code set to '%s' (derived from '%s')", code, market_name)
    return code


def check_market_code_collisions() -> list[tuple[str, list[str]]]:
    """Check if any active markets produce the same derived market code.

    Returns a list of (code, [market_names]) for codes that map to more
    than one market.  An empty list means no collisions.
    """
    from fam.database.connection import get_connection
    conn = get_connection()
    rows = conn.execute("SELECT name FROM markets").fetchall()
    code_to_names: dict[str, list[str]] = {}
    for r in rows:
        code = derive_market_code(r['name'])
        code_to_names.setdefault(code, []).append(r['name'])
    return [(code, names) for code, names in code_to_names.items()
            if len(names) > 1]


# ── Device ID (auto-captured machine fingerprint) ─────────────

def get_device_id() -> str | None:
    """Return the stored device ID, or None if not yet captured."""
    return get_setting('device_id')


def capture_device_id() -> str:
    """Read the Windows MachineGuid and store it. Returns the ID."""
    guid = _read_machine_guid()
    set_setting('device_id', guid)
    return guid


# ── Cloud sync settings ───────────────────────────────────────

def is_sync_configured() -> bool:
    """Return True if Google Sheets sync has credentials and a spreadsheet ID."""
    creds = get_setting('sync_credentials_loaded')
    sheet_id = get_setting('sync_spreadsheet_id')
    return creds == '1' and bool(sheet_id)


def get_sync_spreadsheet_id() -> str | None:
    """Return the Google Sheets spreadsheet ID."""
    return get_setting('sync_spreadsheet_id')


def set_sync_spreadsheet_id(value: str) -> None:
    """Store the Google Sheets spreadsheet ID."""
    set_setting('sync_spreadsheet_id', value.strip())


def get_last_sync_at() -> str | None:
    """Return the ISO timestamp of the last successful sync."""
    return get_setting('last_sync_at')


def get_last_sync_error() -> str | None:
    """Return the error from the last sync attempt, if any."""
    return get_setting('last_sync_error')


# ── Per-tab sync toggles ─────────────────────────────────────

REQUIRED_SYNC_TABS: frozenset[str] = frozenset({
    'Vendor Reimbursement',
    'Detailed Ledger',
    'Error Log',
    'Agent Tracker',
    'Geolocation',
    'FMNP Entries',
})

OPTIONAL_SYNC_TABS: frozenset[str] = frozenset({
    'FAM Match Report',
    'Transaction Log',
    'Activity Log',
    'Market Day Summary',
})


def _sync_tab_key(tab_name: str) -> str:
    """Derive the app_settings key for a tab toggle.

    Example: 'Vendor Reimbursement' -> 'sync_tab_vendor_reimbursement'
    """
    return 'sync_tab_' + tab_name.lower().replace(' ', '_')


def is_sync_tab_enabled(tab_name: str) -> bool:
    """Return True if a sheet tab should be included in sync.

    Required tabs always return True.  Optional tabs are off by default
    and stored as ``sync_tab_<sanitized_name>`` = '1' when enabled.
    """
    if tab_name in REQUIRED_SYNC_TABS:
        return True
    if tab_name in OPTIONAL_SYNC_TABS:
        return get_setting(_sync_tab_key(tab_name), '0') == '1'
    # Unknown tabs: sync by default (forward-compat safety)
    return True


def set_sync_tab_enabled(tab_name: str, enabled: bool) -> None:
    """Persist the sync toggle for an optional tab."""
    if tab_name not in OPTIONAL_SYNC_TABS:
        return
    set_setting(_sync_tab_key(tab_name), '1' if enabled else '0')


# ── Auto-update settings ─────────────────────────────────────

def get_update_repo_url() -> str | None:
    """Return the configured GitHub repository URL for updates."""
    return get_setting('update_repo_url')


def set_update_repo_url(url: str) -> None:
    """Store the GitHub repository URL for updates."""
    set_setting('update_repo_url', url.strip())


def is_auto_update_check_enabled() -> bool:
    """Return True if automatic update checking is enabled (default: True)."""
    return get_setting('update_auto_check', '1') == '1'


def get_last_update_check() -> str | None:
    """Return the ISO timestamp of the last update check."""
    return get_setting('update_last_check')


def set_last_update_check(iso_timestamp: str) -> None:
    """Store the ISO timestamp of the last update check."""
    set_setting('update_last_check', iso_timestamp)


def _read_machine_guid() -> str:
    """Read MachineGuid from the Windows registry. Falls back to hostname."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography"
        )
        value, _ = winreg.QueryValueEx(key, "MachineGuid")
        winreg.CloseKey(key)
        return value
    except Exception:
        import platform
        return f"hostname-{platform.node()}"
