"""Collection Variance correction (ENH-008).

An EBT terminal keying error can log a SNAP amount that differs from
what was actually charged (logged $12.75, card took $12.50).  The
vendor is owed the full receipt (unchanged); FAM's deposit is short;
the SNAP report should reflect the amount actually collected, FAM's
match must stay intact, and the difference is booked as Unallocated
Funds.  ``apply_collection_variance`` does this surgically, bypassing
the payment engine so the match is NOT re-derived from the charge.
"""

import pytest

from fam.database.connection import (
    close_connection, get_connection, set_db_path,
)
from fam.database.schema import initialize_database
from fam.database.seed import seed_sample_data


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    close_connection()
    set_db_path(str(tmp_path / "cv.db"))
    initialize_database()
    seed_sample_data()
    yield
    close_connection()


def _ids():
    conn = get_connection()
    market = conn.execute("SELECT id FROM markets LIMIT 1").fetchone()
    vendor = conn.execute("SELECT id, name FROM vendors LIMIT 1").fetchone()
    snap = conn.execute(
        "SELECT id, name FROM payment_methods WHERE name='SNAP'").fetchone()
    return market, vendor, snap


def _snap_txn(receipt=2550, snap_charge=1275, snap_match=1275):
    """One Confirmed SNAP transaction. Default: receipt $25.50,
    SNAP charge $12.75, match $12.75 (100% match)."""
    from fam.models.market_day import create_market_day
    conn = get_connection()
    market, vendor, snap = _ids()
    md = create_market_day(market['id'], '2026-06-15', opened_by='T')
    cur = conn.execute(
        "INSERT INTO transactions (market_day_id, vendor_id, receipt_total, "
        "status, fam_transaction_id) VALUES (?, ?, ?, 'Confirmed', ?)",
        (md, vendor['id'], receipt, 'FAM-CV-1'))
    txn_id = cur.lastrowid
    conn.execute(
        "INSERT INTO payment_line_items (transaction_id, payment_method_id, "
        "method_name_snapshot, match_percent_snapshot, method_amount, "
        "customer_charged, match_amount) VALUES (?, ?, 'SNAP', 100.0, ?, ?, ?)",
        (txn_id, snap['id'], snap_charge + snap_match, snap_charge, snap_match))
    conn.commit()
    return txn_id, snap['id'], vendor['id']


def _lines(txn_id):
    from fam.models.transaction import get_payment_line_items
    return {li['method_name_snapshot']: li
            for li in get_payment_line_items(txn_id)}


class TestHappyPath:

    def test_bryan_case_12_75_to_12_50(self):
        """The exact field case: SNAP $12.75 logged, $12.50 collected."""
        from fam.models.transaction import apply_collection_variance
        txn_id, snap_id, _ = _snap_txn()

        variance = apply_collection_variance(txn_id, snap_id, 1250)
        assert variance == 25

        lines = _lines(txn_id)
        snap = lines['SNAP']
        # SNAP now reflects the actual collected amount...
        assert snap['customer_charged'] == 1250
        # ...match is FROZEN (the whole point) ...
        assert snap['match_amount'] == 1275
        # ...method drops by the variance.
        assert snap['method_amount'] == 2525
        # The variance is booked as Unallocated Funds.
        uf = lines['Unallocated Funds']
        assert uf['method_amount'] == 25
        assert uf['customer_charged'] == 0
        assert uf['match_amount'] == 0

    def test_vendor_total_unchanged(self):
        """Sum of method_amounts (vendor total) must equal the receipt
        before and after — the vendor is owed exactly the same."""
        from fam.models.transaction import apply_collection_variance
        txn_id, snap_id, _ = _snap_txn(receipt=2550)
        conn = get_connection()
        before = conn.execute(
            "SELECT SUM(method_amount) s FROM payment_line_items "
            "WHERE transaction_id=?", (txn_id,)).fetchone()['s']
        assert before == 2550
        apply_collection_variance(txn_id, snap_id, 1250)
        after = conn.execute(
            "SELECT SUM(method_amount) s FROM payment_line_items "
            "WHERE transaction_id=?", (txn_id,)).fetchone()['s']
        assert after == 2550
        receipt = conn.execute(
            "SELECT receipt_total FROM transactions WHERE id=?",
            (txn_id,)).fetchone()['receipt_total']
        assert receipt == 2550

    def test_reconciliation_identity_holds(self):
        """customer_charged + match + absorbed == receipt."""
        from fam.models.transaction import apply_collection_variance
        txn_id, snap_id, _ = _snap_txn()
        apply_collection_variance(txn_id, snap_id, 1250)
        lines = _lines(txn_id)
        cust = sum(li['customer_charged'] for li in lines.values())
        match = sum(li['match_amount'] for li in lines.values())
        absorbed = lines['Unallocated Funds']['method_amount']
        assert cust == 1250          # matches actual SNAP deposit
        assert match == 1275         # FAM match unchanged
        assert cust + match == 2525
        assert cust + match + absorbed == 2550

    def test_existing_uf_line_is_incremented_not_duplicated(self):
        """A transaction already carrying a UF line gets it incremented,
        not a second UF row added."""
        from fam.models.transaction import (
            apply_collection_variance, get_payment_line_items)
        txn_id, snap_id, _ = _snap_txn()
        apply_collection_variance(txn_id, snap_id, 1250)   # UF = 25
        # A second correction on the same line (now $12.50 → $12.40).
        apply_collection_variance(txn_id, snap_id, 1240)   # +10
        uf_rows = [li for li in get_payment_line_items(txn_id)
                   if li['method_name_snapshot'] == 'Unallocated Funds']
        assert len(uf_rows) == 1
        assert uf_rows[0]['method_amount'] == 35

    def test_zero_percent_method_no_doubling(self):
        """A 0%-match method (Cash) books the exact variance (no match
        to preserve, so absorbed == the raw difference)."""
        from fam.models.transaction import apply_collection_variance
        from fam.models.market_day import create_market_day
        conn = get_connection()
        market, vendor, _ = _ids()
        cash = conn.execute(
            "SELECT id FROM payment_methods WHERE name='Cash'").fetchone()
        md = create_market_day(market['id'], '2026-06-16', opened_by='T')
        cur = conn.execute(
            "INSERT INTO transactions (market_day_id, vendor_id, "
            "receipt_total, status, fam_transaction_id) "
            "VALUES (?, ?, 1000, 'Confirmed', 'FAM-CV-CASH')",
            (md, vendor['id']))
        txn_id = cur.lastrowid
        conn.execute(
            "INSERT INTO payment_line_items (transaction_id, "
            "payment_method_id, method_name_snapshot, match_percent_snapshot, "
            "method_amount, customer_charged, match_amount) "
            "VALUES (?, ?, 'Cash', 0.0, 1000, 1000, 0)",
            (txn_id, cash['id']))
        conn.commit()
        variance = apply_collection_variance(txn_id, cash['id'], 975)
        assert variance == 25
        lines = _lines(txn_id)
        assert lines['Cash']['customer_charged'] == 975
        assert lines['Cash']['match_amount'] == 0
        assert lines['Unallocated Funds']['method_amount'] == 25


class TestGuards:

    def test_over_collection_rejected(self):
        from fam.models.transaction import apply_collection_variance
        txn_id, snap_id, _ = _snap_txn()
        with pytest.raises(ValueError, match="over-collection"):
            apply_collection_variance(txn_id, snap_id, 1300)

    def test_zero_variance_rejected(self):
        from fam.models.transaction import apply_collection_variance
        txn_id, snap_id, _ = _snap_txn()
        with pytest.raises(ValueError, match="already match"):
            apply_collection_variance(txn_id, snap_id, 1275)

    def test_negative_rejected(self):
        from fam.models.transaction import apply_collection_variance
        txn_id, snap_id, _ = _snap_txn()
        with pytest.raises(ValueError, match="negative"):
            apply_collection_variance(txn_id, snap_id, -5)

    def test_denominated_method_rejected(self):
        """A denominated method (Food RX) cannot be under-charged —
        its face value is fixed."""
        from fam.models.transaction import apply_collection_variance
        from fam.models.market_day import create_market_day
        conn = get_connection()
        market, vendor, _ = _ids()
        rx = conn.execute(
            "SELECT id, denomination FROM payment_methods "
            "WHERE name='Food RX'").fetchone()
        assert rx['denomination'] and rx['denomination'] > 0
        md = create_market_day(market['id'], '2026-06-17', opened_by='T')
        cur = conn.execute(
            "INSERT INTO transactions (market_day_id, vendor_id, "
            "receipt_total, status, fam_transaction_id) "
            "VALUES (?, ?, 2000, 'Confirmed', 'FAM-CV-RX')",
            (md, vendor['id']))
        txn_id = cur.lastrowid
        conn.execute(
            "INSERT INTO payment_line_items (transaction_id, "
            "payment_method_id, method_name_snapshot, match_percent_snapshot, "
            "method_amount, customer_charged, match_amount) "
            "VALUES (?, ?, 'Food RX', 100.0, 2000, 1000, 1000)",
            (txn_id, rx['id']))
        conn.commit()
        with pytest.raises(ValueError, match="non-denominated"):
            apply_collection_variance(txn_id, rx['id'], 900)

    def test_voided_transaction_rejected(self):
        from fam.models.transaction import apply_collection_variance
        txn_id, snap_id, _ = _snap_txn()
        conn = get_connection()
        conn.execute("UPDATE transactions SET status='Voided' WHERE id=?",
                     (txn_id,))
        conn.commit()
        with pytest.raises(ValueError, match="voided"):
            apply_collection_variance(txn_id, snap_id, 1250)

    def test_no_matching_line_rejected(self):
        from fam.models.transaction import apply_collection_variance
        txn_id, _, _ = _snap_txn()
        with pytest.raises(ValueError, match="No matching"):
            apply_collection_variance(txn_id, 99999, 1250)


class TestAuditTrail:

    def test_collection_variance_audit_row_written(self):
        from fam.models.transaction import apply_collection_variance
        txn_id, snap_id, _ = _snap_txn()
        apply_collection_variance(txn_id, snap_id, 1250,
                                  changed_by='Bryan',
                                  notes='EBT terminal under-charge')
        conn = get_connection()
        row = conn.execute(
            "SELECT action, changed_by, notes FROM audit_log "
            "WHERE record_id=? AND action='COLLECTION_VARIANCE'",
            (txn_id,)).fetchone()
        assert row is not None
        assert row['changed_by'] == 'Bryan'
        assert 'terminal' in (row['notes'] or '')


class TestDialog:
    """The CollectionVarianceDialog gates OK correctly and returns the
    right values.  The heavy logic is in apply_collection_variance
    (tested above); this pins the dialog's guard UX."""

    def test_dialog_gating_and_values(self, qapp):
        from PySide6.QtWidgets import QDialogButtonBox
        from fam.models.transaction import (
            get_transaction_by_id, get_payment_line_items)
        from fam.ui.admin_screen import CollectionVarianceDialog
        txn_id, snap_id, _ = _snap_txn()
        txn = get_transaction_by_id(txn_id)
        items = [it for it in get_payment_line_items(txn_id)
                 if it['method_name_snapshot'] == 'SNAP']
        dlg = CollectionVarianceDialog(txn, items)
        ok = dlg._buttons.button(QDialogButtonBox.Ok)

        # Pre-filled to the logged value → no change → OK disabled.
        assert ok.isEnabled() is False
        # Under-collection → enabled, values correct.
        dlg.amount_spin.setValue(12.50)
        assert ok.isEnabled() is True
        pm_id, actual = dlg.result_values()
        assert pm_id == snap_id and actual == 1250
        assert 'Unallocated Funds' in dlg.preview.text()
        # Over-collection → disabled with a warning.
        dlg.amount_spin.setValue(13.00)
        assert ok.isEnabled() is False
        assert 'over-collection' in dlg.preview.text()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
