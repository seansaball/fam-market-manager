"""GOLDEN REGRESSION — the FMNP pipeline is frozen (ENH-002, matrix row 6).

The expected values below were hand-captured from the PRE-ENH-002
collector behavior (v2.0.x, verified against data_collector.py
before any external-payments change landed).  They pin, byte for
byte, what existing FMNP data produces on:

  * the ``FMNP Entries`` sheet tab   (_collect_fmnp_entries)
  * ``Vendor Reimbursement``         (_collect_vendor_reimbursement)
  * ``Detailed Ledger`` FMNP rows    (_collect_detailed_ledger)
  * ``FAM Match Report`` FMNP row    (_collect_fam_match)

HARD CONSTRAINT (requirements lock 2026-06-11): existing tabs are
FROZEN.  If a change to any collector makes one of these tests
fail, the change re-shaped frozen output — fix the change, do not
update these expectations.  (New columns appended to Vendor
Reimbursement are allowed under Option B; assertions here check
the frozen columns' values and deliberately tolerate ADDITIVE keys
on VR rows only.)

Entries are created through the real model layer
(``create_fmnp_entry``), so on a v38 schema they carry method id +
snapshots exactly like production rows — proving the backfill-shaped
data reproduces legacy output.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database
from fam.models.fmnp import create_fmnp_entry, delete_fmnp_entry


@pytest.fixture(autouse=True)
def golden_world(tmp_path):
    """Market + vendor + closed market day + FMNP method ($5 denom,
    100% match) + one Active 2-check $10 entry + one Deleted entry."""
    db_file = str(tmp_path / "test_golden.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit)"
        " VALUES (10, 'Bethel Park', 10000)")
    conn.execute(
        "INSERT INTO vendors (id, name, is_active)"
        " VALUES (20, 'Pitaland Inc.', 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status,"
        " opened_by, closed_by, closed_at)"
        " VALUES (40, 10, '2026-04-26', 'Closed', 'T', 'T',"
        " '2026-04-26 16:00:00')")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent,"
        " is_active, sort_order, denomination, photo_required,"
        " external_matching_accepted, vendor_cashes_original)"
        " VALUES (2, 'FMNP', 100.0, 0, 2, 500, 'Optional', 1, 1)")
    conn.commit()

    create_fmnp_entry(40, 20, 1000, 'Coordinator', check_count=2)
    deleted_id = create_fmnp_entry(40, 20, 500, 'Coordinator')
    delete_fmnp_entry(deleted_id, changed_by='Coordinator')

    yield conn
    close_connection()


def _strip_timestamps(rows, key='Timestamp'):
    out = []
    for r in rows:
        r = dict(r)
        assert r.get(key), f"row missing {key}: {r}"
        r.pop(key)
        out.append(r)
    return out


class TestFmnpEntriesTabGolden:
    """REVISED by design revision R1 (Sean, 2026-06-11): the FMNP
    Entries tab is DEPRECATED.  Its collector must return [] so the
    empty upsert DRAINS this device's rows from the shared sheet
    (old-version devices keep writing it until they upgrade; the
    coordinator deletes the empty tab once the fleet is uniform).
    FMNP entries now appear on the External Payment Entries tab —
    pinned below in TestExternalTabCarriesFmnpGolden."""

    def test_collector_drains_the_deprecated_tab(self, golden_world):
        from fam.sync.data_collector import _collect_fmnp_entries
        assert _collect_fmnp_entries(golden_world, 40) == [], (
            "The FMNP Entries collector must return [] — a non-empty "
            "result would re-populate the deprecated tab and break "
            "the drain-then-delete cutover plan (R1).")

    def test_tab_stays_registered_for_the_drain(self):
        """Removing the tab from SHEET_KEYS / collectors /
        REQUIRED_SYNC_TABS before the fleet is uniform would STOP
        the drain and strand stale rows on the shared sheet."""
        from fam.sync.manager import SyncManager
        from fam.utils.app_settings import (
            REQUIRED_SYNC_TABS, is_sync_tab_enabled)
        assert 'FMNP Entries' in SyncManager.SHEET_KEYS
        assert 'FMNP Entries' in REQUIRED_SYNC_TABS
        assert is_sync_tab_enabled('FMNP Entries')

    def test_empty_sync_includes_the_tab_for_drain(self, golden_world):
        """collect_sync_data must still emit the (empty) tab so
        sync_all calls upsert on it — that empty upsert IS the
        drain."""
        from fam.utils.app_settings import set_setting
        set_setting('market_code', 'GLD')
        set_setting('device_id', 'dev-golden')
        from fam.sync.data_collector import collect_sync_data
        data = collect_sync_data(40)
        assert 'FMNP Entries' in data
        assert data['FMNP Entries'] == []


class TestExternalTabCarriesFmnpGolden:
    """R1 golden: the FMNP entry's exact row on the External Payment
    Entries tab.  FAM Owes == face (the match, at the backfill-
    identical 100% / cashes-original snapshots); basis says 'Match
    only' — the payment IS the match, not the face."""

    def test_fmnp_row_exact(self, golden_world):
        from fam.sync.data_collector import (
            _collect_external_payment_entries)
        rows = _strip_timestamps(
            _collect_external_payment_entries(golden_world, 40))
        active = [r for r in rows if r['Status'] == 'Active']
        assert active == [
            {'Entry ID': 'FE-1', 'Market Day Date': '2026-04-26',
             'Vendor': 'Pitaland Inc.', 'Payment Method': 'FMNP',
             'Face Value': 10.0, 'Instruments': 2,
             'Match %': 100.0, 'Vendor Cashes Original': 'Yes',
             'FAM Owes Vendor': 10.0,
             'Reimbursement Basis': 'Match only ($10.00 × 100%)',
             'Status': 'Active', 'Entered By': 'Coordinator',
             'Notes': '', 'Photos': ''},
        ]

    def test_deleted_entry_flagged_voided(self, golden_world):
        from fam.sync.data_collector import (
            _collect_external_payment_entries)
        rows = _collect_external_payment_entries(golden_world, 40)
        voided = [r for r in rows if r['Status'] == 'Voided']
        assert [r['Entry ID'] for r in voided] == ['FE-2']


class TestVendorReimbursementGolden:

    # The frozen VR columns and their exact values for the fixture.
    # Option B allows ADDITIVE columns later (e.g. 'Food RX
    # (External)'), so the assertion checks these keys exactly but
    # tolerates extra keys — any extra key must not change these.
    FROZEN_ROW = {
        'Market Name': 'Bethel Park',
        'Vendor': 'Pitaland Inc.',
        'Month': 'April 2026',
        'Year-Month': '2026-04',
        'Date(s)': '2026-04-26',
        'Total Due to Vendor': 10.0,
        'FAM Match': 0.0,
        'FMNP (External)': 10.0,
        'Customer Forfeit': 0.0,
        'Check Payable To': 'Pitaland Inc.',
        'Address': '',
    }

    def test_fmnp_only_vendor_month_row(self, golden_world):
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement)
        rows = _collect_vendor_reimbursement(golden_world, [40])
        assert len(rows) == 1, rows
        row = rows[0]
        for key, expected in self.FROZEN_ROW.items():
            assert row.get(key) == expected, (
                f"Frozen Vendor Reimbursement column {key!r} "
                f"changed: expected {expected!r}, got "
                f"{row.get(key)!r}.  The FMNP (External) feed and "
                f"all existing column semantics are FROZEN.")

    def test_deleted_entry_excluded_from_totals(self, golden_world):
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement)
        rows = _collect_vendor_reimbursement(golden_world, [40])
        # The Deleted $5 entry must not appear anywhere: Total Due
        # stays $10 (would be $15 if status filtering regressed).
        assert rows[0]['Total Due to Vendor'] == 10.0


class TestDetailedLedgerGolden:

    def test_fmnp_row_shape(self, golden_world):
        from fam.sync.data_collector import _collect_detailed_ledger
        rows = _strip_timestamps(
            _collect_detailed_ledger(golden_world, 40))
        assert rows == [
            {'Transaction ID': 'FMNP-1',
             'Customer': '', 'Zip Code': '',
             'Vendor': 'Pitaland Inc.',
             'Receipt Total': 10.0, 'Customer Paid': 0,
             'FAM Match': 10.0, 'Customer Forfeit': 0,
             'Status': 'FMNP Entry',
             'Payment Methods': 'FMNP (External) - 2 checks'},
        ], ("Detailed Ledger FMNP rows changed — FMNP- ids, the "
            "FAM Match = face convention, and the 'FMNP Entry' "
            "status string are FROZEN.")


class TestFamMatchReportGolden:

    def test_fmnp_external_row(self, golden_world):
        from fam.sync.data_collector import _collect_fam_match
        rows = _collect_fam_match(golden_world, 40)
        assert rows == [
            {'Payment Method': 'FMNP (External)',
             'Date': '2026-04-26',
             'Total Allocated': 10.0,
             'Total FAM Match': 0,
             'FAM Absorbed': 0},
        ], ("FAM Match Report FMNP (External) row changed — this "
            "row's shape and feed are FROZEN.")
