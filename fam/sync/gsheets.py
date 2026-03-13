"""Google Sheets sync backend using gspread."""

import json
import logging
import os
import random
import time
from typing import Optional

from fam.sync.base import SyncBackend, SyncResult

logger = logging.getLogger('fam.sync.gsheets')

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
    """
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as exc:
            # gspread wraps HTTP errors in APIError
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
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


def _cell_value(val) -> str:
    """Convert a Python value to a clean string for Google Sheets.

    Floats are rounded to 2 decimal places to avoid IEEE 754 artifacts
    like ``5.38000000000001`` reaching the sheet.  All monetary values
    in the app use 2-decimal precision, so this is safe for every tab.
    """
    if isinstance(val, float):
        return f"{val:.2f}"
    return str(val)


class GoogleSheetsBackend(SyncBackend):
    """Google Sheets implementation of SyncBackend."""

    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
    ]

    def __init__(self):
        self._client = None
        self._spreadsheet = None

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
            return SyncResult(success=False, error=str(e))

    def upsert_rows(self, sheet_name: str, rows: list[dict],
                    key_columns: list[str]) -> SyncResult:
        """Insert, update, or remove rows by composite key.

        Rows present locally are upserted.  Rows that belong to this
        market/device but are **no longer** in *rows* are deleted from
        the sheet so it always mirrors the local state exactly.
        """
        try:
            _ensure_imports()
            self._authorize()

            # Determine this device's identity for scoping deletes
            from fam.utils.app_settings import get_market_code, get_device_id
            my_mc = str(get_market_code() or '')
            my_did = str(get_device_id() or '')

            if not rows:
                # No local data — remove all rows for this device
                try:
                    ws = self._spreadsheet.worksheet(sheet_name)
                except _gspread.exceptions.WorksheetNotFound:
                    return SyncResult(success=True, rows_synced=0)

                all_values = _retry_on_error(ws.get_all_values)
                _headers, existing = _rows_from_values(all_values)
                to_delete = [
                    row_num for row_num, row in enumerate(existing, start=2)
                    if (str(row.get('market_code', '')) == my_mc and
                        str(row.get('device_id', '')) == my_did)
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

            # Find stale rows: belong to this device but no longer in data
            stale_row_nums = []
            for key, row_num in existing_index.items():
                if key not in incoming_keys:
                    # Only delete rows owned by this device
                    ex_row = existing[row_num - 2]
                    if (str(ex_row.get('market_code', '')) == my_mc and
                            str(ex_row.get('device_id', '')) == my_did):
                        stale_row_nums.append(row_num)

            # Batch update existing rows — only cells that actually changed
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
                    _retry_on_error(lambda: ws.update_cells(dirty_cells))

            unchanged = len(updates) - dirty_row_count

            # Append new rows
            if appends:
                new_rows = [
                    [_cell_value(row.get(h, '')) for h in headers]
                    for row in appends
                ]
                _retry_on_error(
                    lambda: ws.append_rows(new_rows,
                                           value_input_option='RAW'))

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
