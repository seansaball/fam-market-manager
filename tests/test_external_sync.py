"""External payments sync + reporting (ENH-002 P4, matrix rows 7/11).

Covers tab routing by method id, the External Payment Entries tab
shape (self-explanatory rows: snapshots + derived payout + plain-
English basis), the Vendor Reimbursement row identity v2 (EP3),
FAM Match external rows, Detailed Ledger EXT- rows, the ledger
backup's external section, and the mixed-fleet shape (FMNP-only
data produces zero new-tab rows and no new columns).

Fixture money (hand-derived from the locked formula):
  FMNP   $10 face, 100%, cashes-original   → FAM owes $10
  Food RX $20 face, 100%, FAM collects     → FAM owes $40 (match $20)
  Food Bucks $4 face, 0%, FAM collects     → FAM owes $4  (match $0)
  + one VOIDED Food RX $10 entry — excluded from every rollup.
EP3:  0 (booth) + 0 (match) − 0 (forfeit) + $10 + $40 + $4 = $54.
"""

import os

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database
from fam.models.fmnp import create_fmnp_entry, delete_fmnp_entry


@pytest.fixture
def world(tmp_path):
    db_file = str(tmp_path / "test_external_sync.db")
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
        " VALUES (2, 'FMNP', 100.0, 0, 2, 500, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent,"
        " is_active, sort_order, denomination,"
        " external_matching_accepted, vendor_cashes_original)"
        " VALUES (3, 'Food RX', 100.0, 1, 3, 1000, 1, 0)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent,"
        " is_active, sort_order, denomination,"
        " external_matching_accepted, vendor_cashes_original)"
        " VALUES (4, 'JH Food Bucks', 0.0, 1, 4, 200, 1, 0)")
    conn.commit()

    create_fmnp_entry(40, 20, 1000, 'Coordinator', check_count=2)  # id 1
    create_fmnp_entry(40, 20, 2000, 'Coordinator',
                      payment_method_id=3)                          # id 2
    create_fmnp_entry(40, 20, 400, 'Coordinator',
                      payment_method_id=4)                          # id 3
    voided = create_fmnp_entry(40, 20, 1000, 'Coordinator',
                               payment_method_id=3)                 # id 4
    delete_fmnp_entry(voided, changed_by='Coordinator')

    yield conn
    close_connection()


# ──────────────────────────────────────────────────────────────────
# Tab routing
# ──────────────────────────────────────────────────────────────────


class TestTabRouting:
    """REVISED R1 (2026-06-11): the deprecated FMNP Entries tab
    drains (collector returns []); the External Payment Entries tab
    carries ALL methods, FMNP included — one row per entry."""

    def test_fmnp_tab_drains(self, world):
        from fam.sync.data_collector import _collect_fmnp_entries
        assert _collect_fmnp_entries(world, 40) == []

    def test_external_tab_carries_all_methods(self, world):
        from fam.sync.data_collector import (
            _collect_external_payment_entries)
        rows = _collect_external_payment_entries(world, 40)
        assert [r['Entry ID'] for r in rows] == ['FE-1', 'FE-2',
                                                 'FE-3', 'FE-4']
        by_id = {r['Entry ID']: r for r in rows}
        # The FMNP entry rides the same tab, standard shape.
        fmnp = by_id['FE-1']
        assert fmnp['Payment Method'] == 'FMNP'
        assert fmnp['Face Value'] == 10.0
        assert fmnp['FAM Owes Vendor'] == 10.0
        assert fmnp['Vendor Cashes Original'] == 'Yes'
        assert fmnp['Reimbursement Basis'] == (
            'Match only ($10.00 × 100%)')


# ──────────────────────────────────────────────────────────────────
# External Payment Entries tab shape
# ──────────────────────────────────────────────────────────────────


class TestExternalTabShape:

    def test_food_rx_row_is_self_explanatory(self, world):
        from fam.sync.data_collector import (
            _collect_external_payment_entries)
        rows = _collect_external_payment_entries(world, 40)
        rx = next(r for r in rows if r['Entry ID'] == 'FE-2')
        assert rx['Payment Method'] == 'Food RX'
        assert rx['Face Value'] == 20.0
        assert rx['Instruments'] == 2          # derived: $20 / $10
        assert rx['Match %'] == 100.0
        assert rx['Vendor Cashes Original'] == 'No'
        assert rx['FAM Owes Vendor'] == 40.0
        assert rx['Reimbursement Basis'] == (
            "Face + match ($20.00 × 2.0)")
        assert rx['Status'] == 'Active'
        assert rx['Market Day Date'] == '2026-04-26'

    def test_food_bucks_face_only_row(self, world):
        from fam.sync.data_collector import (
            _collect_external_payment_entries)
        rows = _collect_external_payment_entries(world, 40)
        fb = next(r for r in rows if r['Entry ID'] == 'FE-3')
        assert fb['FAM Owes Vendor'] == 4.0
        assert fb['Reimbursement Basis'] == "Face only"
        assert fb['Instruments'] == 2          # derived: $4 / $2

    def test_voided_entry_included_with_status(self, world):
        """Audit-trail semantics (like the sync Detailed Ledger's
        voided transactions): visible on the tab, excluded from
        every rollup."""
        from fam.sync.data_collector import (
            _collect_external_payment_entries)
        rows = _collect_external_payment_entries(world, 40)
        v = next(r for r in rows if r['Entry ID'] == 'FE-4')
        assert v['Status'] == 'Voided'

    def test_snapshot_change_produces_side_by_side_rows(self, world):
        """The finance requirement: a settings correction creates
        old-wrong and new-right rows side by side, each carrying
        its own basis."""
        from fam.models.payment_method import update_payment_method
        from fam.sync.data_collector import (
            _collect_external_payment_entries)
        update_payment_method(3, match_percent=50.0)
        create_fmnp_entry(40, 20, 2000, 'Coordinator',
                          payment_method_id=3)  # id 5, new config
        rows = _collect_external_payment_entries(world, 40)
        old = next(r for r in rows if r['Entry ID'] == 'FE-2')
        new = next(r for r in rows if r['Entry ID'] == 'FE-5')
        assert (old['Match %'], old['FAM Owes Vendor']) == (100.0, 40.0)
        assert (new['Match %'], new['FAM Owes Vendor']) == (50.0, 30.0)
        assert new['Reimbursement Basis'] == (
            "Face + match ($20.00 × 1.5)")


# ──────────────────────────────────────────────────────────────────
# Vendor Reimbursement — EP3 row identity v2
# ──────────────────────────────────────────────────────────────────


class TestVendorReimbursementExternalColumns:

    def test_external_columns_and_total(self, world):
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement)
        rows = _collect_vendor_reimbursement(world, [40])
        assert len(rows) == 1
        row = rows[0]
        assert row['FMNP (External)'] == 10.0
        assert row['Food RX (External)'] == 40.0
        assert row['JH Food Bucks (External)'] == 4.0
        assert row['Total Due to Vendor'] == 54.0

    def test_ep3_row_identity_v2(self, world):
        """Σ(method-cols) + FAM Match − Customer Forfeit
        + FMNP (External) + Σ <Method> (External) == Total Due."""
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement)
        row = _collect_vendor_reimbursement(world, [40])[0]
        meta = {'Market Name', 'Vendor', 'Month', 'Year-Month',
                'Date(s)', 'Total Due to Vendor', 'FAM Match',
                'FMNP (External)', 'Customer Forfeit',
                'Check Payable To', 'Address'}
        ext_cols = [k for k in row if k.endswith(' (External)')
                    and k != 'FMNP (External)']
        method_cols = [k for k in row
                       if k not in meta and k not in ext_cols]
        identity = (sum(row[m] for m in method_cols)
                    + row['FAM Match']
                    - row['Customer Forfeit']
                    + row['FMNP (External)']
                    + sum(row[e] for e in ext_cols))
        assert abs(identity - row['Total Due to Vendor']) < 0.011

    def test_voided_external_entry_excluded(self, world):
        """The $10 voided Food RX entry must not appear anywhere:
        Food RX (External) is $40 (not $50), Total Due $54."""
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement)
        row = _collect_vendor_reimbursement(world, [40])[0]
        assert row['Food RX (External)'] == 40.0
        assert row['Total Due to Vendor'] == 54.0


# ──────────────────────────────────────────────────────────────────
# FAM Match Report + Detailed Ledger
# ──────────────────────────────────────────────────────────────────


class TestFamMatchExternalRows:

    def test_per_method_external_rows(self, world):
        from fam.sync.data_collector import _collect_fam_match
        rows = {r['Payment Method']: r
                for r in _collect_fam_match(world, 40)}
        assert rows['FMNP (External)']['Total Allocated'] == 10.0
        assert rows['FMNP (External)']['Total FAM Match'] == 0
        rx = rows['Food RX (External)']
        assert rx['Total Allocated'] == 40.0
        assert rx['Total FAM Match'] == 20.0
        fb = rows['JH Food Bucks (External)']
        assert fb['Total Allocated'] == 4.0
        assert fb['Total FAM Match'] == 0.0


class TestDetailedLedgerExtRows:

    def test_ext_rows_shape(self, world):
        from fam.sync.data_collector import _collect_detailed_ledger
        rows = {r['Transaction ID']: r
                for r in _collect_detailed_ledger(world, 40)}
        assert rows['FMNP-1']['Status'] == 'FMNP Entry'
        rx = rows['EXT-2']
        assert rx['Receipt Total'] == 40.0
        assert rx['FAM Match'] == 20.0
        assert rx['Customer Paid'] == 0
        assert rx['Status'] == 'External Entry'
        # "- N items" suffix appears only when check_count was
        # entered (matches the FMNP row precedent); this entry's
        # count is derived, not stored.
        assert rx['Payment Methods'] == 'Food RX (External)'
        assert 'EXT-4' not in rows  # voided → excluded


# ──────────────────────────────────────────────────────────────────
# Ledger backup
# ──────────────────────────────────────────────────────────────────


def _ledger_text(tmp_path):
    path = os.path.join(str(tmp_path), 'fam_ledger_backup.txt')
    with open(path, encoding='utf-8') as f:
        return f.read()


class TestLedgerBackupExternalSection:

    def test_external_section_and_grand_total(self, world, tmp_path):
        from fam.utils.export import write_ledger_backup
        write_ledger_backup(force=True)
        text = _ledger_text(tmp_path)
        assert '--- FMNP (External) Entries ---' in text
        assert '--- External Payment Entries ---' in text
        assert 'EXT-2' in text
        assert 'Food RX (External)' in text
        assert 'Face + match ($20.00 × 2.0)' in text
        assert 'Total FMNP (External): $10.00' in text
        # $40 (RX) + $4 (Bucks) — voided RX excluded.
        assert ('Total External Payments (FAM owed): $44.00'
                in text)

    def test_fmnp_only_db_has_no_external_section(self, tmp_path):
        """Byte-compat for FMNP-only databases: no external
        section, no external grand-total line."""
        db_file = str(tmp_path / "fmnp_only.db")
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
            " opened_by) VALUES (40, 10, '2026-04-26', 'Open', 'T')")
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent,"
            " is_active, sort_order, denomination,"
            " external_matching_accepted, vendor_cashes_original)"
            " VALUES (2, 'FMNP', 100.0, 0, 2, 500, 1, 1)")
        conn.commit()
        create_fmnp_entry(40, 20, 1000, 'Coordinator', check_count=2)

        from fam.utils.export import write_ledger_backup
        write_ledger_backup(force=True)
        text = _ledger_text(tmp_path)
        assert '--- FMNP (External) Entries ---' in text
        assert '--- External Payment Entries ---' not in text
        assert 'Total External Payments' not in text
        close_connection()


# ──────────────────────────────────────────────────────────────────
# Mixed fleet + required-tab registration
# ──────────────────────────────────────────────────────────────────


class TestMixedFleetShape:

    def test_fmnp_only_data_produces_no_new_vr_columns(self, world):
        """R1 revision: an FMNP-only market's entries DO appear on
        the External Payment Entries tab now (that's the point of
        the consolidation), but it still contributes no new Vendor
        Reimbursement columns — the FMNP (External) column keeps
        carrying its money, so finance totals are untouched."""
        from fam.sync.data_collector import (
            _collect_external_payment_entries,
            _collect_vendor_reimbursement)
        world.execute(
            "UPDATE fmnp_entries SET status='Deleted'"
            " WHERE payment_method_id != 2")
        world.commit()
        rows = _collect_external_payment_entries(world, 40)
        active = [r for r in rows if r['Status'] == 'Active']
        assert [r['Payment Method'] for r in active] == ['FMNP']
        row = _collect_vendor_reimbursement(world, [40])[0]
        assert 'Food RX (External)' not in row
        assert row['FMNP (External)'] == 10.0
        assert row['Total Due to Vendor'] == 10.0


class TestRequiredTabRegistration:

    def test_external_tab_is_required(self):
        from fam.utils.app_settings import (
            REQUIRED_SYNC_TABS, is_sync_tab_enabled)
        assert 'External Payment Entries' in REQUIRED_SYNC_TABS
        assert is_sync_tab_enabled('External Payment Entries')
