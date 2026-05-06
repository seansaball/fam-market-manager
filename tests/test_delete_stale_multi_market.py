"""Sheet delete-stale cleanup must not over-restrict by market_code
(v2.0.6 fix).

User-reported: voiding the last Confirmed transaction for a vendor
at a market that's NOT the device's currently-configured primary
market left the stale Vendor Reimbursement row in the synced sheet
indefinitely.

Root cause: the cleanup loop in ``GoogleSheetsBackend.upsert_rows``
gated on ``ex_row.market_code == my_mc AND ex_row.device_id ==
my_did``.  Whole-dataset tabs (Vendor Reimbursement) legitimately
emit rows for ALL of this device's markets — but ``my_mc`` is the
single primary market_code from app_settings, so stale rows
belonging to OTHER markets were silently skipped.

Fix: gate by ``device_id`` only.  ``device_id`` correctly identifies
"rows this device owns" regardless of which market they belong to.
The composite-key dedupe (which DOES include market_code) still
prevents accidental cross-device or cross-market collisions when
deciding which rows are stale.

This test pins the new behavior directly via ``upsert_rows``.
"""

from unittest.mock import MagicMock, patch

import pytest


def _make_mock_worksheet(existing_rows: list[dict], headers: list[str]):
    """Build a mock gspread worksheet that behaves like one with
    ``existing_rows`` already in it.  Returns ``(ws, captured)``
    where ``captured`` records the delete_rows / batch_delete calls
    so the test can assert which rows the upsert removed."""
    captured = {
        'deleted_row_nums': [],
        'updated_cells': [],
        'appended': [],
    }

    # Sheet representation: list of lists, header at row 0
    all_values = [headers]
    for r in existing_rows:
        all_values.append([str(r.get(h, '')) for h in headers])

    ws = MagicMock()
    ws.col_count = max(len(headers), 26)
    ws.get_all_values = MagicMock(return_value=all_values)

    def _update_cells(cells):
        captured['updated_cells'].extend(cells)
    ws.update_cells = MagicMock(side_effect=_update_cells)

    def _append_rows(rows, value_input_option=None):
        captured['appended'].extend(rows)
    ws.append_rows = MagicMock(side_effect=_append_rows)

    return ws, captured


class TestStaleRowCleanupGatesByDeviceIdOnly:
    """v2.0.6: stale-row cleanup must delete rows this device owns
    regardless of which market they belong to.  Pre-fix, the gate
    was too narrow (market_code AND device_id) and silently kept
    stale rows from non-primary markets in the sheet."""

    def test_stale_row_in_other_market_is_deleted(self, monkeypatch):
        """Device's primary market_code is BP.  Sheet has two rows
        owned by this device: one at BP, one at BL.  New data omits
        the BL row.  After the fix, the BL row IS deleted (pre-fix
        it was skipped because BL != BP)."""
        from fam.sync.gsheets import GoogleSheetsBackend

        my_did = 'this-device-id'
        my_mc = 'BP'

        backend = GoogleSheetsBackend()
        # Stub the credentials / spreadsheet machinery so upsert_rows
        # doesn't actually call Google.
        backend._spreadsheet = MagicMock()

        # Stub identity lookups
        monkeypatch.setattr(
            'fam.utils.app_settings.get_market_code', lambda: my_mc)
        monkeypatch.setattr(
            'fam.utils.app_settings.get_device_id', lambda: my_did)
        # Skip the gspread import / authorize machinery
        monkeypatch.setattr(backend, '_authorize', lambda: None)
        backend._authorize()  # noqa - confirm callable
        # _ensure_imports populates module globals; do it directly
        from fam.sync import gsheets as gs_mod
        gs_mod._ensure_imports()

        headers = ['market_code', 'device_id', 'Market Name', 'Vendor', 'Total']
        # Sheet existing state: this device wrote two rows at two markets
        existing = [
            {'market_code': 'BP', 'device_id': my_did,
             'Market Name': 'Bethel Park', 'Vendor': 'Juice Bar',
             'Total': '50.00'},
            {'market_code': 'BL', 'device_id': my_did,
             'Market Name': 'Bellevue', 'Vendor': 'Bread Co',
             'Total': '30.00'},
            # And another device's row at BP — must be untouched
            {'market_code': 'BP', 'device_id': 'other-device',
             'Market Name': 'Bethel Park', 'Vendor': 'Juice Bar',
             'Total': '20.00'},
        ]
        ws, captured = _make_mock_worksheet(existing, headers)
        backend._spreadsheet.worksheet = MagicMock(return_value=ws)

        # New collector output: only BP row remains.  The BL row
        # (this device's) is now stale.  The other device's row is
        # also "stale" from this device's perspective but belongs
        # to a different device — must NOT be deleted.
        new_rows = [
            {'market_code': 'BP', 'device_id': my_did,
             'Market Name': 'Bethel Park', 'Vendor': 'Juice Bar',
             'Total': '50.00'},
        ]

        # Track which row numbers _batch_delete_rows is called with
        delete_calls = {'rows': []}
        def _capture_delete(self, ws, row_nums):
            delete_calls['rows'] = list(row_nums)
        monkeypatch.setattr(
            GoogleSheetsBackend, '_batch_delete_rows', _capture_delete)

        backend.upsert_rows(
            'Vendor Reimbursement',
            new_rows,
            key_columns=['market_code', 'device_id', 'Market Name', 'Vendor'],
            delete_stale=True,
        )

        # Sheet rows are 1-indexed with row 1 = headers.
        # existing[0] = row 2 (BP/this-device — kept, in incoming)
        # existing[1] = row 3 (BL/this-device — stale, MUST DELETE)
        # existing[2] = row 4 (BP/other-device — not in incoming but
        #                       belongs to other device, MUST NOT DELETE)
        assert 3 in delete_calls['rows'], (
            f"Expected row 3 (BL/this-device) to be deleted as "
            f"stale.  Got {delete_calls['rows']}.  Pre-fix the "
            f"cleanup gated on market_code == my_mc which silently "
            f"skipped this row because BL != BP (the device's "
            f"primary).")
        assert 4 not in delete_calls['rows'], (
            f"Row 4 (BP/other-device) must NOT be deleted — it "
            f"belongs to another device and this device's cleanup "
            f"has no business removing it.  Got {delete_calls['rows']}.")
        assert 2 not in delete_calls['rows'], (
            f"Row 2 (BP/this-device) is in the new data and must "
            f"not be deleted.  Got {delete_calls['rows']}.")

    def test_other_device_rows_protected_in_empty_data_path(
            self, monkeypatch):
        """The fast-path empty-rows branch (no local data at all)
        must also gate by device_id only — but still protect other
        devices' rows."""
        from fam.sync.gsheets import GoogleSheetsBackend

        my_did = 'this-device-id'
        my_mc = 'BP'

        backend = GoogleSheetsBackend()
        backend._spreadsheet = MagicMock()
        monkeypatch.setattr(
            'fam.utils.app_settings.get_market_code', lambda: my_mc)
        monkeypatch.setattr(
            'fam.utils.app_settings.get_device_id', lambda: my_did)
        monkeypatch.setattr(backend, '_authorize', lambda: None)

        from fam.sync import gsheets as gs_mod
        gs_mod._ensure_imports()

        headers = ['market_code', 'device_id', 'Market Name', 'Vendor']
        existing = [
            # This device, two markets — both should be deleted
            {'market_code': 'BP', 'device_id': my_did,
             'Market Name': 'Bethel Park', 'Vendor': 'V1'},
            {'market_code': 'BL', 'device_id': my_did,
             'Market Name': 'Bellevue', 'Vendor': 'V2'},
            # Other device — must NOT be deleted
            {'market_code': 'BP', 'device_id': 'other',
             'Market Name': 'Bethel Park', 'Vendor': 'V3'},
        ]
        ws, _captured = _make_mock_worksheet(existing, headers)
        backend._spreadsheet.worksheet = MagicMock(return_value=ws)

        delete_calls = {'rows': []}
        def _capture_delete(self, ws, row_nums):
            delete_calls['rows'] = list(row_nums)
        monkeypatch.setattr(
            GoogleSheetsBackend, '_batch_delete_rows', _capture_delete)

        backend.upsert_rows(
            'Vendor Reimbursement',
            rows=[],
            key_columns=['market_code', 'device_id',
                         'Market Name', 'Vendor'],
            delete_stale=True,
        )

        # Both this-device rows should be deleted
        assert 2 in delete_calls['rows']
        assert 3 in delete_calls['rows'], (
            "BL row owned by this device must be deleted in empty-"
            "data path.  Pre-fix it was skipped because BL != BP.")
        # Other-device row stays
        assert 4 not in delete_calls['rows']


class TestNarrowScopeStillSkipsCleanup:
    """v2.0.6 only changed the IDENTITY check inside the cleanup
    branch.  The outer ``if delete_stale:`` gate is unchanged — when
    a narrow-scope auto-sync runs (delete_stale=False) NO rows are
    deleted regardless of device_id.  This is critical: it prevents
    a single-day auto-sync from removing historical rows for that
    same device from other market days."""

    def test_narrow_scope_does_not_delete_anything(self, monkeypatch):
        from fam.sync.gsheets import GoogleSheetsBackend

        my_did = 'this-device-id'
        backend = GoogleSheetsBackend()
        backend._spreadsheet = MagicMock()
        monkeypatch.setattr(
            'fam.utils.app_settings.get_market_code', lambda: 'BP')
        monkeypatch.setattr(
            'fam.utils.app_settings.get_device_id', lambda: my_did)
        monkeypatch.setattr(backend, '_authorize', lambda: None)
        from fam.sync import gsheets as gs_mod
        gs_mod._ensure_imports()

        headers = ['market_code', 'device_id', 'Transaction ID']
        existing = [
            {'market_code': 'BP', 'device_id': my_did,
             'Transaction ID': 'FAM-BP-1'},
            {'market_code': 'BP', 'device_id': my_did,
             'Transaction ID': 'FAM-BP-2'},
        ]
        ws, _captured = _make_mock_worksheet(existing, headers)
        backend._spreadsheet.worksheet = MagicMock(return_value=ws)

        # New data only includes FAM-BP-1.  FAM-BP-2 looks stale.
        new_rows = [
            {'market_code': 'BP', 'device_id': my_did,
             'Transaction ID': 'FAM-BP-1'},
        ]

        delete_calls = {'rows': []}
        monkeypatch.setattr(
            GoogleSheetsBackend, '_batch_delete_rows',
            lambda self, ws, row_nums: delete_calls['rows'].extend(row_nums))

        backend.upsert_rows(
            'Detailed Ledger',
            new_rows,
            key_columns=['market_code', 'device_id', 'Transaction ID'],
            delete_stale=False,  # narrow-scope auto-sync
        )

        assert delete_calls['rows'] == [], (
            "Narrow-scope auto-sync (delete_stale=False) MUST NOT "
            "delete any rows — the per-day collection legitimately "
            "doesn't include rows from other days for this device.")
