"""Google Sheets sync backend using gspread."""

import json
import logging
import os
import random
import socket
import time
from typing import Optional

from fam.sync.base import SyncBackend, SyncResult

logger = logging.getLogger('fam.sync.gsheets')


# ── Offline-error classification ──────────────────────────────
#
# When the laptop loses internet, every sheet tab fails with the
# same DNS / connection-refused chain.  Without classification, a
# 5-minute outage dumps 30+ full tracebacks into the log per sync
# cycle (Bethel Park 2026-05-01 incident).
#
# ``_is_offline_error`` walks the exception's ``__cause__`` /
# ``__context__`` chain looking for known network-unreachable
# signatures.  It's intentionally CONSERVATIVE: only well-known
# transient-network patterns get the quiet treatment.  Anything
# else (auth errors, schema bugs, programming errors) keeps the
# full ``logger.exception`` traceback so we can debug it.

# Windows socket error codes we treat as "offline".  POSIX errno
# values (EAI_*, ENETUNREACH) are caught via ``socket.gaierror``
# isinstance check below, which covers both platforms.
_WINSOCK_OFFLINE_ERRNOS = frozenset({
    11001,  # WSAHOST_NOT_FOUND — DNS failed (the Bethel Park signature)
    11002,  # WSATRY_AGAIN
    11003,  # WSANO_RECOVERY
    11004,  # WSANO_DATA
    10051,  # WSAENETUNREACH
    10065,  # WSAEHOSTUNREACH
    10060,  # WSAETIMEDOUT — connection timeout
    10061,  # WSAECONNREFUSED
})

# Class-name fingerprints that mean "transient network unreachable"
# anywhere in the exception chain.  We match by name rather than
# import to avoid pulling urllib3 / google.auth / requests just to
# do this check (and to stay resilient to library version drift).
_OFFLINE_EXC_NAMES = frozenset({
    'NameResolutionError',     # urllib3.exceptions.NameResolutionError
    'NewConnectionError',      # urllib3.exceptions.NewConnectionError
    'MaxRetryError',           # urllib3.exceptions.MaxRetryError
    'ConnectionError',         # requests.exceptions.ConnectionError
    'ConnectTimeout',          # requests.exceptions.ConnectTimeout
    'ReadTimeoutError',        # urllib3.exceptions.ReadTimeoutError
    'TransportError',          # google.auth.exceptions.TransportError
})


def _is_offline_error(exc: BaseException) -> bool:
    """Return True if *exc* (or any cause/context in its chain)
    looks like a transient network-unreachable failure.

    Walking the chain is necessary because the user-visible
    exception is usually 3-4 wrapper layers above the real cause.
    The Bethel Park 2026-05-01 chain was:

        socket.gaierror [11001]
          → urllib3 NameResolutionError
            → urllib3 MaxRetryError
              → requests.ConnectionError
                → google.auth TransportError

    We accept any of those as evidence of an outage.
    """
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, socket.gaierror):
            return True
        if isinstance(current, (TimeoutError,)):
            # Generic socket timeout — treat as offline-class so we
            # don't dump tracebacks during a flaky-WiFi blip.
            return True
        if isinstance(current, OSError):
            errno_val = getattr(current, 'errno', None)
            winerror_val = getattr(current, 'winerror', None)
            if errno_val in _WINSOCK_OFFLINE_ERRNOS:
                return True
            if winerror_val in _WINSOCK_OFFLINE_ERRNOS:
                return True
        if type(current).__name__ in _OFFLINE_EXC_NAMES:
            return True
        # Walk to the next link in the chain — prefer __cause__
        # (explicit ``raise X from Y``) over __context__ (implicit
        # in-except chaining).  Either path leads us to the gaierror.
        current = current.__cause__ or current.__context__
    return False

# Lazy imports — app works without gspread installed
_gspread = None
_Credentials = None


def _ensure_imports():
    """Import gspread and google-auth on first use."""
    global _gspread, _Credentials
    if _gspread is None:
        import gspread
        from google.oauth2.service_account import Credentials
        _gspread = gspread
        _Credentials = Credentials


def _get_credentials_path() -> str:
    """Return the path to the Google credentials JSON file."""
    from fam.app import get_data_dir
    return os.path.join(get_data_dir(), 'google_credentials.json')


def _retry_on_error(fn, max_retries=5):
    """Retry *fn* with exponential backoff on transient errors.

    Retries on:
    - HTTP 429 (rate limit) — Google Sheets enforces 60 writes/min
    - HTTP 500, 502, 503 (server errors) — transient Google outages
    - ConnectionError / TimeoutError — network blips

    Retries up to *max_retries* times with increasing waits
    (roughly 2s, 5s, 10s, 20s, 40s) to ride out transient failures.

    v2.0.2 fix: explicitly refuse to retry on 4xx client errors
    (other than 429).  ``requests.HTTPError`` inherits from
    ``OSError`` (via ``IOError`` aliasing in the requests package),
    so without this guard the ``isinstance(exc, OSError)`` branch
    below would silently retry permanent 400 / 401 / 403 / 404
    failures five times with backoff — wasting ~80 seconds per
    failed call across ~9 tabs per cycle AND producing a misleading
    "transient error" log line for what is actually a permanent
    malformed-request / auth bug.  Same bug-shape as the v2.0.1
    drive.py fix; the guard is mirrored here.
    """
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as exc:
            # gspread wraps HTTP errors in APIError
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            # Permanent client error → never retry.  Re-raise so the
            # caller sees the real error message and we don't log a
            # misleading "transient error" warning.
            if (status is not None and 400 <= status < 500
                    and status != 429):
                raise
            is_retryable = (
                status in (429, 500, 502, 503) or
                isinstance(exc, (ConnectionError, TimeoutError, OSError))
            )
            if is_retryable and attempt < max_retries - 1:
                wait = (2 ** attempt) + random.uniform(1.0, 3.0)
                logger.warning("Retryable error (status=%s, %s), retrying in %.1fs "
                               "(attempt %d/%d)…", status, type(exc).__name__,
                               wait, attempt + 1, max_retries)
                time.sleep(wait)
            else:
                raise


def _rows_from_values(all_values: list[list[str]]) -> tuple[list[str], list[dict]]:
    """Convert raw ``get_all_values()`` output into (headers, rows-as-dicts).

    Returns ``(headers, existing)`` where *existing* is a list of dicts
    keyed by header name — the same shape as ``get_all_records()``.
    """
    if not all_values:
        return [], []
    headers = all_values[0]
    existing = [
        {headers[i]: (row[i] if i < len(row) else '')
         for i in range(len(headers))}
        for row in all_values[1:]
    ]
    return headers, existing


# ── CSV / Sheets formula-injection escape ───────────────────────
#
# Google Sheets evaluates any cell starting with =, +, -, @, or a
# tab as a formula — *server-side*, on every device viewing the
# sheet.  A vendor named ``=HYPERLINK("evil")`` syncs verbatim and
# evaluates for everyone the spreadsheet is shared with.  Prefix-
# escape with a tab (Sheets strips the leading whitespace on
# display while never evaluating the cell as a formula) — same
# treatment as the CSV export path in fam/utils/export.py.
_SHEETS_DANGEROUS_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def _cell_value(val) -> str:
    """Convert a Python value to a clean string for Google Sheets.

    Floats are rounded to 2 decimal places to avoid IEEE 754 artifacts
    like ``5.38000000000001`` reaching the sheet.  All monetary values
    in the app use 2-decimal precision, so this is safe for every tab.

    String cells starting with a formula trigger are prefixed with
    ``\\t`` to neutralise CSV/Sheets formula injection (OWASP CSV
    Injection).
    """
    if isinstance(val, float):
        return f"{val:.2f}"
    s = str(val)
    if s and s.startswith(_SHEETS_DANGEROUS_PREFIXES):
        return '\t' + s
    return s


class GoogleSheetsBackend(SyncBackend):
    """Google Sheets implementation of SyncBackend."""

    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
    ]

    def __init__(self):
        self._client = None
        self._spreadsheet = None
        # Set to True when an offline error is detected during the
        # current sync cycle.  Subsequent calls in the same cycle
        # short-circuit immediately (no retry, no log) so a 6-tab
        # sync during an internet outage logs ONE warning, not six
        # full tracebacks.  ``reset_offline_state()`` clears this
        # at the start of the next cycle.
        self._offline_this_cycle = False

    def reset_offline_state(self) -> None:
        """Clear the per-cycle offline short-circuit.

        Called by ``SyncManager.sync_all`` at the start of every
        sync attempt so a brief outage doesn't permanently mute
        the next successful cycle.
        """
        self._offline_this_cycle = False

    def _quiet_offline_skip(self, sheet_name: str) -> SyncResult:
        """Return an offline-tagged ``SyncResult`` without logging.

        Used when the backend already logged ONE offline warning
        for this cycle and is now short-circuiting subsequent
        tabs.  Avoids per-tab duplicate logging.
        """
        return SyncResult(
            success=False,
            error="Network unavailable (skipped — will retry next sync)",
            offline=True,
        )

    # ── SyncBackend interface ────────────────────────────────────

    def is_configured(self) -> bool:
        """True if credentials JSON exists and a spreadsheet ID is set."""
        from fam.utils.app_settings import get_sync_spreadsheet_id
        creds_path = _get_credentials_path()
        sheet_id = get_sync_spreadsheet_id()
        return os.path.isfile(creds_path) and bool(sheet_id)

    def validate_connection(self) -> SyncResult:
        """Authorize and try to open the spreadsheet."""
        try:
            _ensure_imports()
            self._authorize()
            title = self._spreadsheet.title
            return SyncResult(success=True, error=None,
                              rows_synced=0)
        except ImportError:
            return SyncResult(success=False,
                              error="gspread not installed")
        except FileNotFoundError:
            return SyncResult(success=False,
                              error="Credentials file not found")
        except Exception as e:
            if _is_offline_error(e):
                logger.warning(
                    "validate_connection: network unavailable "
                    "(%s) — check internet connection",
                    type(e).__name__)
                return SyncResult(
                    success=False,
                    error="Network unavailable",
                    offline=True,
                )
            return SyncResult(success=False, error=str(e))

    def upsert_rows(self, sheet_name: str, rows: list[dict],
                    key_columns: list[str],
                    delete_stale: bool = True) -> SyncResult:
        """Insert, update, or remove rows by composite key.

        Rows present locally are upserted.  Rows that belong to this
        market/device but are **no longer** in *rows* are deleted from
        the sheet so it always mirrors the local state exactly.

        v2.0.1: When *delete_stale* is False, the stale-row cleanup
        is skipped.  Used by narrow-scope auto-syncs (which only
        carry the open market day's data) so they don't delete
        historical rows from prior market days.  Manual full syncs
        still delete-on-shrink as before.
        """
        # Per-cycle short-circuit: if a previous tab in this same
        # sync cycle already failed with a network-class error,
        # skip immediately.  Avoids per-tab traceback spam during
        # an internet outage.
        if self._offline_this_cycle:
            return self._quiet_offline_skip(sheet_name)
        try:
            _ensure_imports()
            self._authorize()

            # Determine this device's identity for scoping deletes
            from fam.utils.app_settings import get_market_code, get_device_id
            my_mc = str(get_market_code() or '')
            my_did = str(get_device_id() or '')

            if not rows:
                # No local data — remove all rows for this device
                # v2.0.6 fix: gate by device_id only.  The previous
                # ``market_code == my_mc`` constraint was too narrow:
                # whole-dataset tabs (Vendor Reimbursement) emit
                # rows from ALL of this device's markets.  When the
                # user voids the last transaction at vendor X in a
                # market different from the device's currently-
                # configured primary market_code, the stale (BL, X)
                # row would never be cleaned up because BL != my_mc
                # (which is BP).  device_id alone correctly scopes
                # the cleanup to "rows this device owns" without
                # over-restricting by current primary market.
                try:
                    ws = self._spreadsheet.worksheet(sheet_name)
                except _gspread.exceptions.WorksheetNotFound:
                    return SyncResult(success=True, rows_synced=0)

                all_values = _retry_on_error(ws.get_all_values)
                _headers, existing = _rows_from_values(all_values)
                to_delete = [
                    row_num for row_num, row in enumerate(existing, start=2)
                    if str(row.get('device_id', '')) == my_did
                ]
                if to_delete:
                    self._batch_delete_rows(ws, to_delete)
                logger.info("Synced %s: removed %d stale rows (no local data)",
                            sheet_name, len(to_delete))
                return SyncResult(success=True, rows_synced=len(to_delete))

            ws = self._get_or_create_worksheet(sheet_name, rows[0])

            all_values = _retry_on_error(ws.get_all_values)
            headers, existing = _rows_from_values(all_values)
            if not headers:
                headers = list(rows[0].keys())
            else:
                # Add any new columns from incoming data to the header row
                data_cols = list(rows[0].keys())
                new_cols = [c for c in data_cols if c not in headers]
                if new_cols:
                    headers.extend(new_cols)
                    # Widen sheet before writing new header cells
                    if len(headers) > ws.col_count:
                        _retry_on_error(
                            lambda: ws.resize(cols=len(headers)))
                    header_cells = [
                        _gspread.Cell(1, i + 1, h)
                        for i, h in enumerate(headers)
                    ]
                    _retry_on_error(lambda: ws.update_cells(header_cells))
                    logger.info("Added %d new columns to '%s': %s",
                                len(new_cols), sheet_name, new_cols)

            # Build index of existing rows by composite key
            existing_index: dict[tuple, int] = {}
            for row_num, row in enumerate(existing, start=2):
                key = tuple(str(row.get(col, '')) for col in key_columns)
                existing_index[key] = row_num

            # Build set of incoming keys
            incoming_keys: set[tuple] = set()
            updates = []
            appends = []

            for row in rows:
                key = tuple(str(row.get(col, '')) for col in key_columns)
                incoming_keys.add(key)
                if key in existing_index:
                    updates.append((existing_index[key], row))
                else:
                    appends.append(row)

            # Find stale rows: belong to this device but no longer in data.
            # Skipped entirely when ``delete_stale=False`` (narrow-scope
            # auto-sync) so a single-day collection cannot remove
            # historical rows from other market days for this same device.
            #
            # v2.0.6 fix: gate by device_id only (was previously
            # ``market_code == my_mc AND device_id == my_did``).
            # Whole-dataset tabs (Vendor Reimbursement) emit rows
            # from ALL of this device's markets.  When the user
            # voids the last Confirmed transaction at vendor X in
            # a market different from the device's currently-
            # configured primary market_code, the (BL, X) row no
            # longer appears in the new collector output — it
            # SHOULD be deleted.  Pre-fix the cleanup gated on
            # ``ex_row.market_code == my_mc`` (=BP) which silently
            # skipped the (BL, X) row, leaving stale data in the
            # sheet indefinitely.  device_id alone correctly
            # scopes the cleanup to "rows this device owns"
            # regardless of which market they belong to.
            stale_row_nums = []
            if delete_stale:
                for key, row_num in existing_index.items():
                    if key not in incoming_keys:
                        # Only delete rows owned by this device
                        ex_row = existing[row_num - 2]
                        if str(ex_row.get('device_id', '')) == my_did:
                            stale_row_nums.append(row_num)

            # Batch update existing rows — only cells that actually changed.
            #
            # v2.0.3 fix (HIGH-1): chunk large payloads.  The Sheets API
            # accepts a single ``values.batchUpdate`` of up to ~10K cells
            # before returning HTTP 413 / 400.  After a long offline
            # period at year-2 scale (50K dirty cells across the full
            # transactions tab is realistic), pre-fix the single
            # ``update_cells(dirty_cells)`` call would fail and the
            # 4xx-aware retry logic (correctly) refuses to retry —
            # leaving "Sync failed" with no path forward.  Chunk to
            # 5000 cells / 1000 rows per call so even a worst-case
            # post-outage sync recovers without manual intervention.
            CELL_CHUNK = 5000
            ROW_CHUNK = 1000

            dirty_cells = []
            dirty_row_count = 0
            if updates:
                for row_num, row in updates:
                    ex_row = existing[row_num - 2]
                    row_dirty = False
                    for col_idx, header in enumerate(headers):
                        new_val = _cell_value(row.get(header, ''))
                        old_val = str(ex_row.get(header, ''))
                        if new_val != old_val:
                            dirty_cells.append(_gspread.Cell(
                                row_num, col_idx + 1, new_val))
                            row_dirty = True
                    if row_dirty:
                        dirty_row_count += 1
                if dirty_cells:
                    # Chunked update: each batch is a single API call.
                    for i in range(0, len(dirty_cells), CELL_CHUNK):
                        chunk = dirty_cells[i:i + CELL_CHUNK]
                        _retry_on_error(
                            lambda c=chunk: ws.update_cells(c))

            unchanged = len(updates) - dirty_row_count

            # Append new rows — chunked for the same reason.
            if appends:
                new_rows = [
                    [_cell_value(row.get(h, '')) for h in headers]
                    for row in appends
                ]
                for i in range(0, len(new_rows), ROW_CHUNK):
                    chunk = new_rows[i:i + ROW_CHUNK]
                    _retry_on_error(
                        lambda c=chunk: ws.append_rows(
                            c, value_input_option='RAW'))

            # Delete stale rows in a single batch API call
            if stale_row_nums:
                self._batch_delete_rows(ws, stale_row_nums)

            total = dirty_row_count + len(appends)
            logger.info(
                "Synced %s: %d updated, %d unchanged, %d appended, %d removed",
                sheet_name, dirty_row_count, unchanged, len(appends),
                len(stale_row_nums))
            return SyncResult(success=True, rows_synced=total)

        except Exception as e:
            if _is_offline_error(e):
                # First offline failure of this sync cycle.  Log
                # ONE concise warning (no traceback) and arm the
                # short-circuit so subsequent tabs don't log
                # again.  The summary line in SyncManager.sync_all
                # rolls everything up.
                if not self._offline_this_cycle:
                    logger.warning(
                        "upsert_rows for %s skipped: network "
                        "unavailable (%s — will retry next sync)",
                        sheet_name, type(e).__name__)
                    self._offline_this_cycle = True
                return SyncResult(
                    success=False,
                    error="Network unavailable",
                    offline=True,
                )
            logger.exception("upsert_rows failed for %s", sheet_name)
            return SyncResult(success=False, error=str(e))

    def delete_rows(self, sheet_name: str,
                    market_code: str, device_id: str) -> SyncResult:
        """Delete all rows matching market_code + device_id."""
        try:
            _ensure_imports()
            self._authorize()

            try:
                ws = self._spreadsheet.worksheet(sheet_name)
            except _gspread.exceptions.WorksheetNotFound:
                return SyncResult(success=True, rows_synced=0)

            all_values = _retry_on_error(ws.get_all_values)
            _hdrs, all_rows = _rows_from_values(all_values)
            rows_to_delete = []
            for row_num, row in enumerate(all_rows, start=2):
                if (str(row.get('market_code', '')) == market_code and
                        str(row.get('device_id', '')) == device_id):
                    rows_to_delete.append(row_num)

            # Delete all matching rows in a single batch API call
            if rows_to_delete:
                self._batch_delete_rows(ws, rows_to_delete)

            logger.info("Deleted %d rows from %s for %s/%s",
                        len(rows_to_delete), sheet_name,
                        market_code, device_id)
            return SyncResult(success=True,
                              rows_synced=len(rows_to_delete))

        except Exception as e:
            if _is_offline_error(e):
                logger.warning(
                    "delete_rows for %s skipped: network "
                    "unavailable (%s)", sheet_name, type(e).__name__)
                return SyncResult(
                    success=False,
                    error="Network unavailable",
                    offline=True,
                )
            logger.exception("delete_rows failed for %s", sheet_name)
            return SyncResult(success=False, error=str(e))

    def read_rows(self, sheet_name: str,
                  market_code: Optional[str] = None,
                  device_id: Optional[str] = None) -> list[dict]:
        """Read rows, optionally filtered by identity."""
        try:
            _ensure_imports()
            self._authorize()

            try:
                ws = self._spreadsheet.worksheet(sheet_name)
            except _gspread.exceptions.WorksheetNotFound:
                return []

            all_values = _retry_on_error(ws.get_all_values)
            _hdrs, rows = _rows_from_values(all_values)

            if market_code:
                rows = [r for r in rows
                        if str(r.get('market_code', '')) == market_code]
            if device_id:
                rows = [r for r in rows
                        if str(r.get('device_id', '')) == device_id]

            return rows

        except Exception:
            logger.exception("read_rows failed for %s", sheet_name)
            return []

    # ── Internal helpers ─────────────────────────────────────────

    def _batch_delete_rows(self, ws, row_nums: list[int]):
        """Delete multiple rows in a single API call via batch_update.

        Uses ``deleteDimension`` requests sorted descending so that
        higher-numbered rows are removed first and lower-numbered row
        indices remain valid throughout the operation.
        """
        sheet_id = ws._properties['sheetId']
        requests = []
        for rn in sorted(row_nums, reverse=True):
            requests.append({
                'deleteDimension': {
                    'range': {
                        'sheetId': sheet_id,
                        'dimension': 'ROWS',
                        'startIndex': rn - 1,  # 0-based inclusive
                        'endIndex': rn,         # 0-based exclusive
                    }
                }
            })
        _retry_on_error(
            lambda: self._spreadsheet.batch_update({'requests': requests})
        )

    def _authorize(self):
        """Authenticate and open the spreadsheet (cached per instance)."""
        if self._spreadsheet is not None:
            return

        creds_path = _get_credentials_path()
        if not os.path.isfile(creds_path):
            raise FileNotFoundError(
                f"Credentials file not found: {creds_path}")

        from fam.utils.app_settings import get_sync_spreadsheet_id
        sheet_id = get_sync_spreadsheet_id()
        if not sheet_id:
            raise ValueError("No spreadsheet ID configured")

        creds = _Credentials.from_service_account_file(
            creds_path, scopes=self.SCOPES)
        self._client = _gspread.authorize(creds)
        self._spreadsheet = self._client.open_by_key(sheet_id)

    def _get_or_create_worksheet(self, name: str,
                                 sample_row: dict):
        """Get an existing worksheet or create one with headers."""
        try:
            ws = self._spreadsheet.worksheet(name)
        except _gspread.exceptions.WorksheetNotFound:
            ws = _retry_on_error(
                lambda: self._spreadsheet.add_worksheet(
                    title=name, rows=1000, cols=len(sample_row)))
            headers = list(sample_row.keys())
            _retry_on_error(
                lambda: ws.append_row(headers, value_input_option='RAW'))
            logger.info("Created worksheet '%s' with %d columns",
                        name, len(headers))
        return ws


def validate_credentials_file(filepath: str) -> tuple[bool, str]:
    """Validate a Google service account JSON file before copying.

    Returns (is_valid, message).
    """
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except json.JSONDecodeError:
        return False, "Invalid JSON file"
    except Exception as e:
        return False, f"Cannot read file: {e}"

    if data.get('type') != 'service_account':
        return False, "Not a service account credentials file"
    if not data.get('client_email'):
        return False, "Missing client_email field"
    if not data.get('private_key'):
        return False, "Missing private_key field"

    return True, f"Valid — service account: {data['client_email']}"
