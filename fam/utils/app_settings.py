"""Centralized helpers for the app_settings key-value table."""

import logging

from fam.database.connection import get_connection

logger = logging.getLogger('fam.utils.app_settings')

# ── Default values ────────────────────────────────────────────
DEFAULT_LARGE_RECEIPT_THRESHOLD = 100.00
DEFAULT_REPO_URL = "https://github.com/seansaball/fam-market-manager"

# ── Update repo URL allow-list (security) ─────────────────────
#
# v2.0.2 fix: pin the auto-update channel to the official repo.
#
# Pre-v2.0.2, ``set_update_repo_url`` accepted ANY github.com URL
# with no constraint that it match the official owner.  A malicious
# `.fam` import file or a rogue Sheets-synced setting could redirect
# the auto-update channel to an attacker-controlled repo.  Combined
# with the absence of Authenticode signing and SHA256 manifests,
# the next "update" downloads, extracts via ``Expand-Archive``, and
# ``xcopy /E /Y``s over the install directory — a one-shot
# RCE-as-installer for any attacker with one-time write access to
# a peer's settings table.
#
# The allow-list contains the canonical (owner, repo) tuples that
# the auto-update channel will accept.  Any other value is silently
# rejected on save and ignored on read (falls back to DEFAULT_REPO_URL).
ALLOWED_UPDATE_REPOS: tuple[tuple[str, str], ...] = (
    ("seansaball", "fam-market-manager"),
)


def _is_allowed_repo_url(url: str) -> bool:
    """Return True if *url* parses to an allow-listed (owner, repo).

    v2.0.3 fix (MED-SEC-1): explicitly reject ``http://`` (cleartext)
    URLs even when they parse to an allow-listed owner/repo.  The
    ``check_for_update`` HTTP call is hardcoded to ``https://api...``
    so this is a defense-in-depth gap — but if any future code path
    were to reuse the saved URL directly for an HTTP fetch (e.g. a
    changelog scrape), the http variant would be a downgrade
    opportunity.  Slam the door at allow-list time.
    """
    if not url:
        return False
    cleaned = url.strip()
    lowered = cleaned.lower()
    # Reject explicit http:// — only allow https:// or no scheme.
    # No-scheme is acceptable because the consumers of the saved
    # value (settings_screen ``_check_for_updates``,
    # ``main_window._auto_check_for_updates``) construct the
    # ``https://api.github.com/...`` URL from the parsed owner/repo,
    # not from the raw saved string.
    if lowered.startswith('http://'):
        return False
    # Lazy import to avoid a circular dependency
    from fam.update.checker import parse_github_repo_url
    parsed = parse_github_repo_url(cleaned)
    if parsed is None:
        return False
    owner, repo = parsed
    # Match case-insensitively on owner (GitHub usernames are
    # case-insensitive) but exact-match on repo (repos preserve case).
    for allowed_owner, allowed_repo in ALLOWED_UPDATE_REPOS:
        if (owner.lower() == allowed_owner.lower()
                and repo == allowed_repo):
            return True
    return False


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
    # ``get_connection`` is at module level (line 5).
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
    """Return the stored device ID, or None if not yet captured.

    v2.0.2 fix (B-H8): the ``hostname-XXX`` fallback is reported as
    ``None`` so the v1.9.10 startup hard-fail catches it.  Cloned
    fleet laptops that share a hostname would otherwise silently
    collide on this value across all instances.
    """
    raw = get_setting('device_id')
    if _is_hostname_fallback_id(raw):
        return None
    return raw


def capture_device_id() -> str:
    """Read the Windows MachineGuid and store it. Returns the ID.

    v2.0.2 fix (B-H8): the ``hostname-XXX`` fallback is treated as
    NOT-A-DEVICE-ID for the purpose of the v1.9.10 hard-fail check at
    ``fam/app.py``.  Pre-fix, ``_read_machine_guid`` returned
    ``f"hostname-{platform.node()}"`` on ANY registry exception —
    almost never empty — so the hard-fail at app startup never fired,
    and image-cloned fleet laptops with the same hostname silently
    produced ``hostname-DESKTOP-ABC123`` for ALL of them, colliding
    on ``device_id`` and corrupting cross-device cloud sync.

    The fallback string is still RETURNED here (so the call site can
    log it) but ``get_device_id`` will treat it as empty / not yet
    captured, surfacing the configuration problem instead of papering
    over it.
    """
    guid = _read_machine_guid()
    set_setting('device_id', guid)
    return guid


def _is_hostname_fallback_id(value: str | None) -> bool:
    """Return True if *value* is the ``hostname-XXX`` synthetic
    fallback rather than a real MachineGuid.

    Used by ``get_device_id`` and the v1.9.10 startup hard-fail to
    refuse cross-device collisions on cloned fleet laptops.
    """
    return bool(value and value.startswith('hostname-'))


# ── Device tag (short suffix on customer labels) ──────────────
#
# v1.9.9: in multi-laptop deployments at one market, every device
# was independently generating C-001, C-002, C-003... — leading to
# coordination collisions ("look up C-005" was ambiguous across 5
# laptops) and synced-Sheets rows that were technically separated by
# device_id but visually duplicated for humans reading the report.
#
# The fix: every customer_label now carries a 3-char device tag —
# either auto-derived from the device's MachineGuid (stable,
# zero-config) or a friendly override the coordinator types in
# Settings (e.g. "LB1" for "Laptop 1").  The tag turns "C-005" into
# "C-005-A1B" / "C-005-LB1", uniquely identifying which laptop
# captured the order regardless of which market it landed at.

# Override key in app_settings.  When unset, the auto-derived hash
# tag is used.  Stored upper-cased; cleared by storing an empty
# string (set_device_tag_override(None)).
_DEVICE_TAG_OVERRIDE_KEY = 'device_tag_override'

# Validation rules for the override.  3 chars matches the auto-
# derived length and keeps customer labels visually consistent;
# allowing 1-4 lets coordinators pick short physical-laptop names
# like "L1" or longer ones like "MGR1" without rewriting the format.
_DEVICE_TAG_MIN_LEN = 1
_DEVICE_TAG_MAX_LEN = 4

# Fallback when no device_id is captured yet — should never happen
# in production (capture happens at app startup before any
# customer_order can be created), but a deterministic fallback
# beats raising in label generation.
_DEVICE_TAG_FALLBACK = 'X00'


def _auto_device_tag(device_id: str | None) -> str:
    """Hash *device_id* to a stable 3-char uppercase tag.

    Hex output of SHA1, first 3 chars uppercased — gives 4096 unique
    tag values (16^3), so collision probability across the realistic
    universe of FAM laptops (single-digit count per market, low
    double digits per org) is negligible.  Fall back to a fixed
    sentinel when device_id is missing so callers never see ``None``.
    """
    if not device_id:
        return _DEVICE_TAG_FALLBACK
    import hashlib
    digest = hashlib.sha1(device_id.encode('utf-8')).hexdigest()
    return digest[:3].upper()


def get_device_tag() -> str:
    """Return the active device tag for new customer labels.

    Resolution order:
      1. Manual override stored in ``app_settings`` (if set, validated)
      2. Auto-derived hash of the captured ``device_id``
      3. Fixed fallback ``'X00'`` (only if device_id is missing —
         indicates ``capture_device_id`` hasn't run yet)

    The result is always upper-cased and 1-4 alphanumeric chars,
    safe to embed directly in a customer label.
    """
    override = get_setting(_DEVICE_TAG_OVERRIDE_KEY)
    if override:
        cleaned = override.strip().upper()
        if (_DEVICE_TAG_MIN_LEN <= len(cleaned) <= _DEVICE_TAG_MAX_LEN
                and cleaned.isalnum()):
            return cleaned
        # Stored override is invalid (manual DB edit, perhaps).
        # Fall through to the auto-derived tag rather than emitting
        # a malformed label.
        logger.warning(
            "Stored device_tag_override %r is invalid — "
            "falling back to auto-derived tag", override)
    return _auto_device_tag(get_device_id())


def get_device_tag_override() -> str | None:
    """Return the manual override exactly as stored, or None if not
    set.  Used by the Settings UI to populate the editor field."""
    raw = get_setting(_DEVICE_TAG_OVERRIDE_KEY)
    return raw.strip().upper() if raw else None


def set_device_tag_override(tag: str | None) -> None:
    """Set or clear the manual device-tag override.

    *tag* of ``None`` (or empty/whitespace) clears the override —
    callers fall back to the auto-derived hash.  Otherwise the value
    must be 1-4 alphanumeric characters; invalid input raises
    ``ValueError`` so the Settings UI can surface a useful message.
    """
    if tag is None or not tag.strip():
        # Clear by deleting the row outright — simpler than storing
        # an empty value and special-casing it in the read path.
        try:
            conn = get_connection()
            conn.execute(
                "DELETE FROM app_settings WHERE key = ?",
                (_DEVICE_TAG_OVERRIDE_KEY,),
            )
            conn.commit()
        except Exception:
            logger.warning("Could not clear device_tag_override",
                           exc_info=True)
        return

    cleaned = tag.strip().upper()
    if not (_DEVICE_TAG_MIN_LEN <= len(cleaned) <= _DEVICE_TAG_MAX_LEN):
        raise ValueError(
            f"Device tag must be {_DEVICE_TAG_MIN_LEN}-"
            f"{_DEVICE_TAG_MAX_LEN} characters")
    if not cleaned.isalnum():
        raise ValueError(
            "Device tag must be letters and digits only "
            "(no spaces or punctuation)")
    set_setting(_DEVICE_TAG_OVERRIDE_KEY, cleaned)


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
    # Generated Rewards (v1.9.10) — required by default per the
    # 2026-04-30 spec.  Coordinators can suppress upload through
    # the Settings → Cloud Sync tab if they want to keep it local.
    'Generated Rewards',
})

OPTIONAL_SYNC_TABS: frozenset[str] = frozenset({
    'FAM Match Report',
    'Transaction Log',
    'Activity Log',
    'Market Day Summary',
})


# ── Rewards program (v1.9.10+) ───────────────────────────────────
#
# Master on/off for the customer-facing rewards add-on.  When
# ``False``, the rewards section in the payment-confirmation dialog
# and on the receipt is suppressed and the Generated Rewards report
# is empty — but the rule config in ``reward_rules`` is preserved
# so toggling back on restores previous behaviour without re-typing.
def is_rewards_enabled() -> bool:
    """True when the rewards program is active globally."""
    return get_setting('rewards_enabled', '1') == '1'


def set_rewards_enabled(enabled: bool) -> None:
    set_setting('rewards_enabled', '1' if enabled else '0')


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
    """Return the configured GitHub repository URL for updates.

    v2.0.2 fix: enforce the allow-list at READ time as well as at
    write time.  A persisted value that doesn't match the allow-list
    (could happen if a malicious ``.fam`` import slipped past an
    older save path, or if the row was edited via a direct DB
    write) is treated as if unset — callers fall back to
    ``DEFAULT_REPO_URL``.  This is the second line of defense
    against auto-update channel hijack.
    """
    saved = get_setting('update_repo_url')
    if saved and not _is_allowed_repo_url(saved):
        logger.warning(
            "Stored update_repo_url %r is not on the allow-list; "
            "ignoring and using DEFAULT_REPO_URL.", saved)
        return None
    return saved


def set_update_repo_url(url: str) -> None:
    """Store the GitHub repository URL for updates.

    v2.0.2 fix: refuse to persist any URL that is not on the
    ``ALLOWED_UPDATE_REPOS`` allow-list.  Raises ``ValueError`` so
    the UI surfaces the rejection — silent rejection would let an
    attacker test a list of URLs without feedback.
    """
    cleaned = url.strip() if url else ''
    if not _is_allowed_repo_url(cleaned):
        raise ValueError(
            f"Refusing to set update_repo_url to {cleaned!r}: not on "
            f"the allow-list of approved release channels.  "
            f"Allowed: {ALLOWED_UPDATE_REPOS}"
        )
    set_setting('update_repo_url', cleaned)


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
