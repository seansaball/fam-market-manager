"""Centralized helpers for the app_settings key-value table."""

import logging

from fam.database.connection import get_connection

logger = logging.getLogger('fam.utils.app_settings')

# ── Default values ────────────────────────────────────────────
DEFAULT_LARGE_RECEIPT_THRESHOLD = 100.00


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


# ── Device ID (auto-captured machine fingerprint) ─────────────

def get_device_id() -> str | None:
    """Return the stored device ID, or None if not yet captured."""
    return get_setting('device_id')


def capture_device_id() -> str:
    """Read the Windows MachineGuid and store it. Returns the ID."""
    guid = _read_machine_guid()
    set_setting('device_id', guid)
    return guid


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
