"""Local reset preserves OTHER devices' rows on the shared cloud
sheet (v2.0.6 multi-workstation safety).

Coordinator-reported (2026-05-06): after a reset of one device, the
sync status indicator showed "failed" until they opened a new market
day, which finally triggered a sync.  Two issues:

1. Reset didn't emit ``settings_changed``, so no auto-sync fired.
   The cleanup was deferred until the next unrelated trigger.
2. Until that deferred sync ran, this device's stale rows remained
   on the shared Google Sheet — and any other workstation looking
   at the sheet during that gap saw stale data attributed to the
   reset device.

Fix:
  * ``_reset_to_default`` now emits ``settings_changed`` after a
    successful wipe, so ``MainWindow._on_settings_changed`` runs an
    immediate ``_trigger_sync(force=True)``.
  * The cleanup gate in ``upsert_rows`` (already gated by device_id
    only as of the v2.0.6 multi-market fix) deletes only rows
    owned by THIS device_id, regardless of which market they
    belong to.  Other devices' rows are preserved.

This file pins both halves of the fix.
"""

import inspect
from unittest.mock import MagicMock

import pytest


# ─── Reset emits the sync signal ────────────────────────────────


class TestResetEmitsSettingsChanged:
    """Source-pin: ``_reset_to_default`` must call
    ``self.settings_changed.emit()`` after a successful wipe so the
    cloud cleanup runs immediately."""

    def test_reset_calls_settings_changed_emit(self):
        import fam.ui.settings_screen as ss
        src = inspect.getsource(ss.SettingsScreen._reset_to_default)
        assert 'self.settings_changed.emit()' in src, (
            "_reset_to_default must emit settings_changed after a "
            "successful wipe.  Pre-fix the reset left local empty "
            "but did NOT trigger a cloud sync, so this device's "
            "stale rows lingered on the shared sheet until some "
            "other action (market day open, manual sync) fired a "
            "sync.  The status indicator showed 'failed' from the "
            "previous run until that happened, causing confusion.")

    def test_reset_emit_is_after_successful_commit(self):
        """The emit must happen INSIDE the success branch — never
        on the rollback / error path.  An emit on a failed reset
        would trigger a sync on a half-wiped DB which is worse
        than no sync at all."""
        import fam.ui.settings_screen as ss
        src = inspect.getsource(ss.SettingsScreen._reset_to_default)
        # Find the position of 'RELEASE reset_all' (success commit
        # marker) and the emit.  Emit must appear AFTER it.
        commit_idx = src.find('conn.execute("RELEASE reset_all")')
        emit_idx = src.find('self.settings_changed.emit()')
        assert commit_idx > -1, "expected SAVEPOINT release marker"
        assert emit_idx > commit_idx, (
            "settings_changed.emit() must follow the SAVEPOINT "
            "RELEASE so it only fires on a successful reset.  "
            "Emitting before the commit would trigger a sync on "
            "a partially-wiped DB.")


# ─── Cleanup preserves other devices' rows ──────────────────────


def _make_mock_worksheet(existing_rows: list[dict],
                         headers: list[str]):
    """Build a mock gspread worksheet pre-populated with
    ``existing_rows``."""
    captured = {'deleted_row_nums': []}

    all_values = [headers]
    for r in existing_rows:
        all_values.append([str(r.get(h, '')) for h in headers])

    ws = MagicMock()
    ws.col_count = max(len(headers), 26)
    ws.get_all_values = MagicMock(return_value=all_values)
    return ws, captured


class TestEmptyDataCleanupPreservesOtherDevices:
    """Direct test of the empty-rows path in
    ``GoogleSheetsBackend.upsert_rows``: when local data is empty
    (post-reset state), the cleanup must delete only rows owned by
    THIS device_id and leave everyone else's rows alone."""

    def test_vendor_reimbursement_other_devices_preserved(
            self, monkeypatch):
        """The killer case: Vendor Reimbursement is a whole-dataset
        tab keyed on (market_code, device_id, Market Name, Vendor).
        Two devices, three markets between them, post-reset cleanup
        on device A must touch only A's rows."""
        from fam.sync.gsheets import GoogleSheetsBackend

        my_did = 'device-A'
        my_mc = 'BP'

        backend = GoogleSheetsBackend()
        backend._spreadsheet = MagicMock()

        monkeypatch.setattr(
            'fam.utils.app_settings.get_market_code',
            lambda: my_mc)
        monkeypatch.setattr(
            'fam.utils.app_settings.get_device_id',
            lambda: my_did)
        monkeypatch.setattr(backend, '_authorize', lambda: None)

        from fam.sync import gsheets as gs_mod
        gs_mod._ensure_imports()

        headers = ['market_code', 'device_id', 'Market Name',
                   'Vendor', 'Total']
        # Mixed rows across two devices and multiple markets.  A
        # critical case is "device A's row at a market different
        # from its current primary" — pre-v2.0.6 that row would have
        # been silently kept (gate was market_code AND device_id).
        existing = [
            # Device A at BP (its primary market)
            {'market_code': 'BP', 'device_id': my_did,
             'Market Name': 'Bethel Park', 'Vendor': 'V1',
             'Total': '50.00'},
            # Device A at BL (NOT its primary — must still be
            # deleted post-reset since it's A's row)
            {'market_code': 'BL', 'device_id': my_did,
             'Market Name': 'Bellevue', 'Vendor': 'V2',
             'Total': '30.00'},
            # Device B at BP (same market as A — must be PRESERVED)
            {'market_code': 'BP', 'device_id': 'device-B',
             'Market Name': 'Bethel Park', 'Vendor': 'V3',
             'Total': '20.00'},
            # Device B at SQ (different market — must be PRESERVED)
            {'market_code': 'SQ', 'device_id': 'device-B',
             'Market Name': 'Squirrel Hill', 'Vendor': 'V4',
             'Total': '15.00'},
        ]
        ws, _captured = _make_mock_worksheet(existing, headers)
        backend._spreadsheet.worksheet = MagicMock(return_value=ws)

        delete_calls = {'rows': []}
        def _capture_delete(self, ws, row_nums):
            delete_calls['rows'] = list(row_nums)
        monkeypatch.setattr(
            GoogleSheetsBackend, '_batch_delete_rows',
            _capture_delete)

        # Empty rows = post-reset state
        backend.upsert_rows(
            'Vendor Reimbursement',
            rows=[],
            key_columns=['market_code', 'device_id',
                         'Market Name', 'Vendor'],
            delete_stale=True,
        )

        # Sheet rows are 1-indexed, row 1 = headers.
        # Row 2 = device A at BP (DELETE)
        # Row 3 = device A at BL (DELETE — non-primary market)
        # Row 4 = device B at BP (PRESERVE)
        # Row 5 = device B at SQ (PRESERVE)
        assert sorted(delete_calls['rows']) == [2, 3], (
            f"Empty-data cleanup must delete EXACTLY device A's "
            f"two rows (rows 2 and 3) and preserve device B's "
            f"rows (rows 4 and 5).  Got "
            f"{sorted(delete_calls['rows'])}.  This is the "
            f"multi-workstation safety guarantee — coordinators "
            f"running multiple devices on the same shared sheet "
            f"must be able to reset one device without nuking "
            f"the others' data.")

    def test_per_md_tab_cleanup_also_device_scoped(
            self, monkeypatch):
        """Same gate must apply to per-md tabs (Detailed Ledger,
        FAM Match, etc.) when the post-reset sync hits them with
        empty data."""
        from fam.sync.gsheets import GoogleSheetsBackend

        my_did = 'device-A'
        backend = GoogleSheetsBackend()
        backend._spreadsheet = MagicMock()

        monkeypatch.setattr(
            'fam.utils.app_settings.get_market_code', lambda: 'BP')
        monkeypatch.setattr(
            'fam.utils.app_settings.get_device_id', lambda: my_did)
        monkeypatch.setattr(backend, '_authorize', lambda: None)

        from fam.sync import gsheets as gs_mod
        gs_mod._ensure_imports()

        headers = ['market_code', 'device_id', 'Transaction ID',
                   'Total']
        existing = [
            {'market_code': 'BP', 'device_id': my_did,
             'Transaction ID': 'FAM-BP-1', 'Total': '10.00'},
            {'market_code': 'BP', 'device_id': 'device-B',
             'Transaction ID': 'FAM-BP-2', 'Total': '20.00'},
            {'market_code': 'SQ', 'device_id': my_did,
             'Transaction ID': 'FAM-SQ-1', 'Total': '30.00'},
        ]
        ws, _captured = _make_mock_worksheet(existing, headers)
        backend._spreadsheet.worksheet = MagicMock(return_value=ws)

        delete_calls = {'rows': []}
        monkeypatch.setattr(
            GoogleSheetsBackend, '_batch_delete_rows',
            lambda self, ws, row_nums:
                delete_calls['rows'].extend(row_nums))

        backend.upsert_rows(
            'Detailed Ledger',
            rows=[],
            key_columns=['market_code', 'device_id',
                         'Transaction ID'],
            delete_stale=True,
        )

        # Row 2 (A/BP) DELETE, Row 3 (B/BP) PRESERVE, Row 4 (A/SQ)
        # DELETE
        assert sorted(delete_calls['rows']) == [2, 4], (
            f"Per-md tab cleanup must also gate by device_id "
            f"only — A's rows at BOTH markets get deleted, B's "
            f"row at BP is preserved.  Got "
            f"{sorted(delete_calls['rows'])}.")


# ─── End-to-end wiring verified ─────────────────────────────────


class TestResetEmitReachesFullSync:
    """The whole chain: reset emits settings_changed →
    main_window._on_settings_changed → _trigger_sync(force=True).
    A force=True sync runs full-scope (no md-id override) which
    hits the empty-rows cleanup path on whole-dataset tabs."""

    def test_chain_routes_to_force_true_trigger(self):
        # Already pinned by tests in test_settings_changed_signal.py
        # and test_sync_invariant_matrix.py — but verify here too
        # so this scenario is covered as a single end-to-end story.
        import fam.ui.main_window as mw
        slot_src = inspect.getsource(
            mw.MainWindow._on_settings_changed)
        assert '_trigger_sync(force=True)' in slot_src, (
            "_on_settings_changed must call "
            "_trigger_sync(force=True).  After reset, this is the "
            "path that drives the empty-data cleanup on whole-"
            "dataset tabs (Vendor Reimbursement, Error Log) — a "
            "narrow per-md sync would skip them entirely and "
            "leave this device's rows on the shared sheet.")
