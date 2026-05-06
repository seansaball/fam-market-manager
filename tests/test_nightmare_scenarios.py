"""Nightmare-scenario stress tests — adversarial financial-integrity audit.

Discipline
----------
* Do NOT assume correctness because prior tests passed.
* Do NOT fix anything from this file — only document.
* Use isolated test data only.
* Prioritize *real-world* failure scenarios over toy edge cases.
* Every scenario asserts at least one **financial invariant** from
  the contract documented in ``docs/FINANCIAL_FORMULA.md``.

Financial invariants (the contract)
-----------------------------------
For every confirmed/adjusted transaction T:

    I1. customer_charged + match_amount = method_amount        (per line)
    I2. Σ method_amount = receipt_total ±0¢                    (per txn)

For every market day D:

    I3. Σ T.receipt_total over D
        == Σ Vendor Reimbursement row totals
        == Σ FAM Match "Total Allocated"
        == Σ Detailed Ledger non-voided receipt_total
    I4. Voided txns excluded from financial reports
    I5. Σ FAM match for any customer ≤ daily_match_limit (when active)
    I6. No DB row has a negative monetary field

For every audit/log surface:

    I7. CREATE/CONFIRM/PAYMENT_SAVED/VOID/ADJUST fire on the
        appropriate lifecycle transitions
    I8. Audit log entries are append-only (no deletions detected)
"""

import csv
import io
import sqlite3

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database
from fam.utils.calculations import (
    calculate_payment_breakdown, charge_to_method_amount,
)
from fam.utils.money import dollars_to_cents


# ════════════════════════════════════════════════════════════════════
# Reusable invariant assertions
# ════════════════════════════════════════════════════════════════════

def assert_per_line_invariant(conn, txn_id):
    """I1: customer_charged + match_amount = method_amount per line."""
    rows = conn.execute(
        "SELECT id, method_amount, match_amount, customer_charged "
        "FROM payment_line_items WHERE transaction_id=?",
        (txn_id,)).fetchall()
    for r in rows:
        assert r['customer_charged'] + r['match_amount'] == r['method_amount'], (
            f"I1 violated: txn={txn_id} pli={r['id']} "
            f"customer({r['customer_charged']}) + match({r['match_amount']}) "
            f"!= method({r['method_amount']})"
        )


def assert_txn_reconciles(conn, txn_id):
    """I2: Σ method_amount = receipt_total."""
    t = conn.execute(
        "SELECT receipt_total, status FROM transactions WHERE id=?",
        (txn_id,)).fetchone()
    method_sum = conn.execute(
        "SELECT COALESCE(SUM(method_amount), 0) FROM payment_line_items "
        "WHERE transaction_id=?", (txn_id,)).fetchone()[0]
    assert method_sum == t['receipt_total'], (
        f"I2 violated: txn={txn_id} status={t['status']} "
        f"receipt={t['receipt_total']} sum(method)={method_sum}"
    )


def assert_no_negative_amounts(conn):
    """I6: no DB row has a negative monetary field."""
    n = conn.execute("""
        SELECT COUNT(*) FROM payment_line_items
        WHERE method_amount < 0 OR match_amount < 0
          OR customer_charged < 0
    """).fetchone()[0]
    assert n == 0, f"I6 violated: {n} payment_line_items have negative amounts"
    n = conn.execute("""
        SELECT COUNT(*) FROM transactions WHERE receipt_total < 0
    """).fetchone()[0]
    assert n == 0, f"I6 violated: {n} transactions have negative receipt_total"


def assert_reports_match_db(conn, market_day_id):
    """I3 + I4: report surfaces equal DB ground truth (excl voided)."""
    db_receipt = conn.execute("""
        SELECT COALESCE(SUM(receipt_total), 0) FROM transactions
        WHERE market_day_id=? AND status IN ('Confirmed', 'Adjusted')
    """, (market_day_id,)).fetchone()[0]

    from fam.sync.data_collector import (
        _collect_vendor_reimbursement, _collect_fam_match,
        _collect_detailed_ledger,
    )
    vr_total = round(sum(r['Total Due to Vendor']
                          for r in _collect_vendor_reimbursement(conn, [market_day_id]))
                     * 100)
    fm_alloc = round(sum(r['Total Allocated']
                          for r in _collect_fam_match(conn, market_day_id))
                     * 100)
    dl_rows = _collect_detailed_ledger(conn, market_day_id)
    dl_total = round(sum(r.get('Receipt Total', 0)
                          for r in dl_rows
                          if r.get('Status') != 'Voided') * 100)

    assert vr_total == db_receipt, (
        f"I3 (Vendor Reimbursement): csv={vr_total}c db={db_receipt}c")
    assert fm_alloc == db_receipt, (
        f"I3 (FAM Match Allocated): csv={fm_alloc}c db={db_receipt}c")
    assert dl_total == db_receipt, (
        f"I3 (Detailed Ledger): csv={dl_total}c db={db_receipt}c")


# ════════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def nightmare_db(tmp_path):
    """8-vendor, 6-method market with $200 daily match cap.

    Cap is intentionally LOWER than the production stress fixture
    so cap-straddling scenarios actually hit it.  Receipts and
    method mix lifted from the user's onsite session.
    """
    db_file = str(tmp_path / "nightmare.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()

    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'NM', 20000, 1)")
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (2, 'NM2', 20000, 1)")

    vendors = [
        (1, 'Apple Orchard'), (2, 'Bakery Plus'), (3, 'Cidery Lane'),
        (4, 'Dumpling Dynasty'), (5, 'Egg Farm'), (6, 'Fresh Fish'),
        (7, 'Greens & Things'), (8, 'Honey Pot'),
    ]
    for vid, name in vendors:
        conn.execute("INSERT INTO vendors (id, name) VALUES (?, ?)",
                     (vid, name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))

    methods = [
        (1, 'SNAP',          100.0, None, 1),
        (2, 'Cash',            0.0, None, 2),
        (3, 'Food RX',        50.0, None, 3),  # fractional cents
        (4, 'JH Food Bucks', 100.0, 200,  4),  # $2 denom
        (5, 'FMNP',          100.0, 500,  5),  # $5 denom
        (6, 'Premium Match', 200.0, None, 6),  # rare 2x
    ]
    for mid, name, pct, denom, sort_o in methods:
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            " denomination, sort_order, is_active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (mid, name, pct, denom, sort_o))
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, "
            " payment_method_id) VALUES (1, ?)", (mid,))

    # Permissive eligibility (every vendor accepts every method)
    # so we can drive *any* combination.  Vendor 7 has restricted
    # eligibility for the eligibility-bypass test.
    for vid in (1, 2, 3, 4, 5, 6, 8):
        for mid in (1, 2, 3, 4, 5, 6):
            conn.execute(
                "INSERT INTO vendor_payment_methods (vendor_id, "
                " payment_method_id) VALUES (?, ?)", (vid, mid))
    # Vendor 7 only accepts SNAP + Cash
    for mid in (1, 2):
        conn.execute(
            "INSERT INTO vendor_payment_methods (vendor_id, "
            " payment_method_id) VALUES (7, ?)", (mid,))

    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-29', 'Open', 'T')")
    conn.commit()
    yield conn
    close_connection()


def _li(method_id, name, pct, method_amt, match_amt, charge):
    return {
        'payment_method_id': method_id,
        'method_name_snapshot': name,
        'match_percent_snapshot': pct,
        'method_amount': method_amt,
        'match_amount': match_amt,
        'customer_charged': charge,
    }


def _confirm(conn, txn_id, items, mark_adjusted=False):
    from fam.models.transaction import (
        save_payment_line_items, confirm_transaction,
    )
    from fam.models.customer_order import update_customer_order_status
    save_payment_line_items(txn_id, items, commit=False)
    confirm_transaction(txn_id, confirmed_by='T', commit=False)
    row = conn.execute(
        "SELECT customer_order_id FROM transactions WHERE id=?",
        (txn_id,)).fetchone()
    if row and row[0] is not None:
        update_customer_order_status(row[0], 'Confirmed', commit=False)
    if mark_adjusted:
        conn.execute(
            "UPDATE transactions SET status='Adjusted' WHERE id=?",
            (txn_id,))
    conn.commit()


# ════════════════════════════════════════════════════════════════════
# CATEGORY A: Adjustment math parity & sequential adjust chain
# ════════════════════════════════════════════════════════════════════

class TestCategoryA_AdjustmentParity:
    """Adversarial: does adjustment math diverge from initial save?"""

    def test_a1_save_then_immediately_readjust_no_drift(self, nightmare_db):
        """Save once → re-save the *exact same* line items via the
        adjustment-style ``save_payment_line_items``.  DB rows must
        be byte-for-byte identical."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
        )
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-A1')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=10000,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
        items = [_li(1, 'SNAP', 100.0, 10000, 5000, 5000)]
        _confirm(nightmare_db, tid, items)

        before = nightmare_db.execute(
            "SELECT method_amount, match_amount, customer_charged "
            "FROM payment_line_items WHERE transaction_id=? "
            "ORDER BY id", (tid,)).fetchall()
        # Re-save same items (mimics adjustment with no changes).
        save_payment_line_items(tid, items)
        after = nightmare_db.execute(
            "SELECT method_amount, match_amount, customer_charged "
            "FROM payment_line_items WHERE transaction_id=? "
            "ORDER BY id", (tid,)).fetchall()
        # Old PLIs are deleted + re-inserted, so IDs differ.  But
        # every monetary field must match exactly.
        before_vals = [(r['method_amount'], r['match_amount'],
                         r['customer_charged']) for r in before]
        after_vals = [(r['method_amount'], r['match_amount'],
                        r['customer_charged']) for r in after]
        assert before_vals == after_vals
        assert_per_line_invariant(nightmare_db, tid)
        assert_txn_reconciles(nightmare_db, tid)

    def test_a2_five_consecutive_no_op_resaves(self, nightmare_db):
        """Re-save the same items 5 times.  Each re-save must
        produce the exact same DB state."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
        )
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-A2')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=2, receipt_total=12345,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
        # Awkward fractional-match scenario: 50% match.
        # $123.45 @ 50% match: charge = $123.45 / 1.5 = $82.30
        # method = $123.45, match = $41.15
        items = [_li(3, 'Food RX', 50.0, 12345, 4115, 8230)]
        _confirm(nightmare_db, tid, items)
        snapshot_1 = self._snapshot(nightmare_db, tid)
        for _ in range(5):
            save_payment_line_items(tid, items)
        snapshot_after = self._snapshot(nightmare_db, tid)
        assert snapshot_1 == snapshot_after, (
            "Sequential no-op re-saves drifted: "
            f"before={snapshot_1} after={snapshot_after}")
        assert_per_line_invariant(nightmare_db, tid)
        assert_txn_reconciles(nightmare_db, tid)

    def _snapshot(self, conn, tid):
        return tuple(sorted(
            (r['method_amount'], r['match_amount'], r['customer_charged'])
            for r in conn.execute(
                "SELECT method_amount, match_amount, customer_charged "
                "FROM payment_line_items WHERE transaction_id=?",
                (tid,)).fetchall()
        ))


# ════════════════════════════════════════════════════════════════════
# CATEGORY B: Receipt-total mutation after confirm
# ════════════════════════════════════════════════════════════════════

class TestCategoryB_ReceiptMutation:
    """Adversarial: change receipt_total after confirm — what survives?"""

    def test_b1_increase_receipt_total_without_resaving_plis(
            self, nightmare_db):
        """Bug seed: receipt goes from $100 → $120 but the PLIs
        still sum to $100.  The per-txn invariant is violated until
        the coordinator re-saves payments.

        This test PROVES the invariant breaks — by design, the UI
        forces re-save, but this verifies the DB does not silently
        protect us if a script bypassed the UI.
        """
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import (
            create_transaction, update_transaction,
        )
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-B1')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=10000,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
        _confirm(nightmare_db, tid,
                 [_li(2, 'Cash', 0.0, 10000, 0, 10000)])
        # Mutate receipt_total directly via the model
        update_transaction(tid, receipt_total=12000)
        # Per-line invariant still holds (no PLI was touched).
        assert_per_line_invariant(nightmare_db, tid)
        # BUT per-txn reconciliation now fails.
        with pytest.raises(AssertionError):
            assert_txn_reconciles(nightmare_db, tid)

    def test_b2_decrease_receipt_total_below_plis(
            self, nightmare_db):
        """receipt $100 → $80, PLIs still $100.  No DB-level
        guard prevents this; the UI is supposed to.
        """
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import (
            create_transaction, update_transaction,
        )
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-B2')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=10000,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
        _confirm(nightmare_db, tid,
                 [_li(2, 'Cash', 0.0, 10000, 0, 10000)])
        update_transaction(tid, receipt_total=8000)
        with pytest.raises(AssertionError):
            assert_txn_reconciles(nightmare_db, tid)


# ════════════════════════════════════════════════════════════════════
# CATEGORY C: Match cap straddling and boundary
# ════════════════════════════════════════════════════════════════════

class TestCategoryC_CapStraddling:

    def test_c1_cap_exactly_at_limit(self, nightmare_db):
        """Customer exactly hits $200 cap.  No drift, no over-cap."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-C1')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=40000,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
        # SNAP @ 100%: $400 receipt = $200 charge + $200 match
        _confirm(nightmare_db, tid,
                 [_li(1, 'SNAP', 100.0, 40000, 20000, 20000)])
        assert_per_line_invariant(nightmare_db, tid)
        assert_txn_reconciles(nightmare_db, tid)
        # Customer's match = $200, exactly at cap.
        from fam.models.customer_order import get_customer_prior_match
        assert get_customer_prior_match('C-C1', 1) == 20000

    def test_c2_cap_one_cent_under(self, nightmare_db):
        """Customer used $199.99 of match.  Next $0.02 should
        be capped to $0.01 (or $0.00 depending on rounding)."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-C2')
        # First transaction: $399.98 @ 100% = $199.99 match
        tid1, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=39998,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
        _confirm(nightmare_db, tid1,
                 [_li(1, 'SNAP', 100.0, 39998, 19999, 19999)])

        # Second transaction with cap-aware engine
        prior = 19999
        remaining_cap = 20000 - prior  # 1¢ left
        # $0.02 @ 100% normally = $0.01 match.  With 1¢ remaining
        # cap, the breakdown should show match_was_capped=False
        # (since 1¢ ≤ 1¢ remaining).
        bd = calculate_payment_breakdown(
            2, [{'method_amount': 2, 'match_percent': 100.0}],
            match_limit=remaining_cap)
        assert bd['is_valid']
        assert bd['fam_subsidy_total'] <= remaining_cap

    def test_c3_cap_zero_full_method_passthrough(self, nightmare_db):
        """match_limit=0 means NO match.  Customer pays full receipt."""
        bd = calculate_payment_breakdown(
            10000,
            [{'method_amount': 10000, 'match_percent': 100.0}],
            match_limit=0)
        assert bd['is_valid']
        # Customer must pay the full 10000.
        li = bd['line_items'][0]
        assert li['customer_charged'] == 10000
        assert li['match_amount'] == 0

    def test_c4_cap_one_cent_total(self, nightmare_db):
        """match_limit=1¢ — only 1¢ of match available across
        all rows.  Stress: $100 receipt, 100% match nominal,
        cap forces customer to pay $99.99."""
        bd = calculate_payment_breakdown(
            10000,
            [{'method_amount': 10000, 'match_percent': 100.0}],
            match_limit=1)
        assert bd['is_valid']
        assert bd['fam_subsidy_total'] <= 1
        li = bd['line_items'][0]
        assert li['customer_charged'] + li['match_amount'] == 10000


# ════════════════════════════════════════════════════════════════════
# CATEGORY D: Multi-method extreme
# ════════════════════════════════════════════════════════════════════

class TestCategoryD_MultiMethodExtreme:

    def test_d1_all_six_methods_one_transaction(self, nightmare_db):
        """All 6 methods on a single $300 transaction at vendor 1."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-D1')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=30000,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
        # Build a 6-method allocation summing to exactly $300:
        # SNAP $100 (50+50), Cash $30 (30+0), Food RX $30 (20+10),
        # Food Bucks $20 ($2 × 5 = 5+5), FMNP $30 (3 × $5 = 15+15),
        # Premium Match $90 (30+60). Total method = $300.
        items = [
            _li(1, 'SNAP',          100.0, 10000, 5000, 5000),
            _li(2, 'Cash',            0.0, 3000,     0, 3000),
            _li(3, 'Food RX',        50.0, 3000,  1000, 2000),
            _li(4, 'JH Food Bucks', 100.0, 2000,  1000, 1000),
            _li(5, 'FMNP',          100.0, 3000,  1500, 1500),
            _li(6, 'Premium Match', 200.0, 9000,  6000, 3000),
        ]
        # Sanity: sum(method_amount) == receipt
        assert sum(i['method_amount'] for i in items) == 30000
        _confirm(nightmare_db, tid, items)
        assert_per_line_invariant(nightmare_db, tid)
        assert_txn_reconciles(nightmare_db, tid)

    def test_d2_two_denom_methods_same_vendor(self, nightmare_db):
        """FMNP + Food Bucks on the SAME transaction (single vendor)."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-D2')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=4000,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
        items = [
            _li(4, 'JH Food Bucks', 100.0, 2000, 1000, 1000),
            _li(5, 'FMNP',          100.0, 2000, 1000, 1000),
        ]
        _confirm(nightmare_db, tid, items)
        assert_per_line_invariant(nightmare_db, tid)
        assert_txn_reconciles(nightmare_db, tid)


# ════════════════════════════════════════════════════════════════════
# CATEGORY E: Vendor re-attribution (eligibility bypass risk)
# ════════════════════════════════════════════════════════════════════

class TestCategoryE_VendorReattribution:
    """Survey finding: update_transaction(vendor_id=...) does not
    validate that the new vendor accepts the existing line items'
    payment methods.  This tests *whether* that's a real risk."""

    def test_e1_reattribute_to_vendor_that_doesnt_accept_method(
            self, nightmare_db):
        """v1.9.10 (Finding E1 fix): re-attributing a transaction to
        a vendor that doesn't accept all of the transaction's
        payment methods is rejected at the model layer."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import (
            create_transaction, update_transaction,
        )
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-E1')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=2000,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
        _confirm(nightmare_db, tid,
                 [_li(4, 'JH Food Bucks', 100.0, 2000, 1000, 1000)])
        # Vendor 7 only accepts SNAP + Cash, NOT Food Bucks.
        with pytest.raises(ValueError, match="does not accept"):
            update_transaction(tid, vendor_id=7)
        # Original vendor unchanged.
        assert nightmare_db.execute(
            "SELECT vendor_id FROM transactions WHERE id=?",
            (tid,)).fetchone()[0] == 1

    def test_e2_reattribute_to_eligible_vendor_succeeds(
            self, nightmare_db):
        """v1.9.10: a re-attribution to a vendor that DOES accept
        all payment methods on the txn must succeed cleanly."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import (
            create_transaction, update_transaction,
        )
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-E2')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=2000,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
        _confirm(nightmare_db, tid,
                 [_li(2, 'Cash', 0.0, 2000, 0, 2000)])
        # Vendor 2 accepts Cash — re-attribution must succeed.
        update_transaction(tid, vendor_id=2)
        assert nightmare_db.execute(
            "SELECT vendor_id FROM transactions WHERE id=?",
            (tid,)).fetchone()[0] == 2


# ════════════════════════════════════════════════════════════════════
# CATEGORY F: Void cascades + re-confirm protection
# ════════════════════════════════════════════════════════════════════

class TestCategoryF_VoidCascades:

    def test_f1_void_excludes_from_prior_match(self, nightmare_db):
        """Customer hits cap; void earlier visit; cap should free."""
        from fam.models.customer_order import (
            create_customer_order, get_customer_prior_match,
            update_customer_order_status,
        )
        from fam.models.transaction import (
            create_transaction, void_transaction,
        )
        # Visit 1: $200 match
        ord1, _ = create_customer_order(
            market_day_id=1, customer_label='C-F1')
        t1, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=40000,
            customer_order_id=ord1,
            market_day_date='2026-04-29')
        _confirm(nightmare_db, t1,
                 [_li(1, 'SNAP', 100.0, 40000, 20000, 20000)])
        assert get_customer_prior_match('C-F1', 1) == 20000
        # Void
        void_transaction(t1, voided_by='T')
        update_customer_order_status(ord1, 'Voided')
        # Cap is now fully free
        assert get_customer_prior_match('C-F1', 1) == 0

    def test_f2_re_confirm_voided_transaction(self, nightmare_db):
        """v1.9.10 (Finding H-2 fix): Voided transactions are
        terminal at the model layer.  Voided -> Confirmed must
        raise ValueError.  Voided -> Voided is idempotent.
        """
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import (
            create_transaction, void_transaction, update_transaction,
        )
        ord1, _ = create_customer_order(
            market_day_id=1, customer_label='C-F2')
        t1, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=5000,
            customer_order_id=ord1,
            market_day_date='2026-04-29')
        _confirm(nightmare_db, t1,
                 [_li(1, 'SNAP', 100.0, 5000, 2500, 2500)])
        void_transaction(t1, voided_by='T')

        # Voided -> Confirmed must be rejected.
        with pytest.raises(ValueError, match="Voided"):
            update_transaction(t1, status='Confirmed')
        # Voided -> Adjusted must be rejected.
        with pytest.raises(ValueError, match="Voided"):
            update_transaction(t1, status='Adjusted')
        # Voided -> Draft must be rejected.
        with pytest.raises(ValueError, match="Voided"):
            update_transaction(t1, status='Draft')

        # Voided -> Voided is idempotent (no-op).
        update_transaction(t1, status='Voided')

        # Status remains Voided after all attempts.
        status = nightmare_db.execute(
            "SELECT status FROM transactions WHERE id=?",
            (t1,)).fetchone()[0]
        assert status == 'Voided'

    def test_f3_void_after_multiple_adjustments(self, nightmare_db):
        """Adjust 3 times, then void.  Audit chain must be intact."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
            void_transaction,
        )
        from fam.models.audit import log_action
        ord1, _ = create_customer_order(
            market_day_id=1, customer_label='C-F3')
        t1, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=10000,
            customer_order_id=ord1,
            market_day_date='2026-04-29')
        _confirm(nightmare_db, t1,
                 [_li(1, 'SNAP', 100.0, 10000, 5000, 5000)])
        # 3 adjustments
        for i in range(3):
            nightmare_db.execute(
                "UPDATE transactions SET status='Adjusted' "
                "WHERE id=?", (t1,))
            log_action('transactions', t1, 'ADJUST', 'T',
                       notes=f'adj {i}')
            save_payment_line_items(
                t1, [_li(1, 'SNAP', 100.0, 10000, 5000, 5000)])
        # Void
        void_transaction(t1, voided_by='T')
        # Audit chain: at minimum CREATE + CONFIRM + 3 ADJUST +
        # 4 PAYMENT_SAVED + VOID
        n = nightmare_db.execute(
            "SELECT COUNT(*) FROM audit_log WHERE record_id=?",
            (t1,)).fetchone()[0]
        assert n >= 9


# ════════════════════════════════════════════════════════════════════
# CATEGORY G: CSV / Excel formula injection (security)
# ════════════════════════════════════════════════════════════════════

class TestCategoryG_CSVInjection:
    """Survey finding: pandas ``to_csv`` does not prefix-escape ``=``,
    ``+``, ``-``, ``@`` formula characters.  These tests examine the
    actual *parsed cell values* (not raw substring) to determine
    whether Excel/Google Sheets would evaluate them as formulas.

    Excel evaluates a cell as a formula when its **post-CSV-parsing
    value** starts with ``=``, ``+``, ``-``, ``@``, or a tab.  CSV
    double-quote wrapping does NOT prevent this — only prepending a
    safe char (``'`` or ``\\t``) does.
    """

    # Excel evaluates a cell as a formula only when its first
    # character is one of these.  A LEADING TAB is the standard
    # OWASP safe-escape: Excel strips the tab on display and never
    # evaluates the cell as a formula.  So a cell starting with
    # ``\\t`` is safe, even if its second character is ``=``.
    DANGEROUS_PREFIXES = ('=', '+', '-', '@')

    def _scan_cells_for_formula_injection(self, csv_path):
        """Parse the CSV and return list of (row_idx, col_name, value)
        tuples for any cell whose post-parse value would be evaluated
        by Excel/Google Sheets as a formula.  Cells already escaped
        with a leading tab are SAFE and not flagged."""
        risky = []
        with open(csv_path, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row_idx, row in enumerate(reader):
                for col, val in row.items():
                    if val and isinstance(val, str) and \
                            val.startswith(self.DANGEROUS_PREFIXES):
                        risky.append((row_idx, col, val))
        return risky

    def test_g1_vendor_name_with_formula_prefix_in_csv(
            self, nightmare_db, tmp_path):
        from fam.sync.data_collector import _collect_vendor_reimbursement
        from fam.utils.export import export_vendor_reimbursement
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction

        nightmare_db.execute(
            "INSERT INTO vendors (id, name) VALUES "
            "(99, '=CMD(\"calc.exe\",0)')")
        nightmare_db.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, 99)")
        for mid in (1, 2):
            nightmare_db.execute(
                "INSERT INTO vendor_payment_methods (vendor_id, "
                " payment_method_id) VALUES (99, ?)", (mid,))
        nightmare_db.commit()
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-G1')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=99, receipt_total=1000,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
        _confirm(nightmare_db, tid,
                 [_li(2, 'Cash', 0.0, 1000, 0, 1000)])

        rows = _collect_vendor_reimbursement(nightmare_db, [1])
        out = str(tmp_path / 'inj.csv')
        export_vendor_reimbursement(rows, out)

        risky = self._scan_cells_for_formula_injection(out)
        if risky:
            details = "\n  ".join(
                f"row {r}, col '{c}': {v!r}" for r, c, v in risky)
            pytest.fail(
                "SECURITY FINDING (CSV/formula injection):\n  "
                f"{details}\n"
                "These cells will EXECUTE as formulas when opened "
                "in Excel or imported to Google Sheets.  CSV "
                "double-quote wrapping does NOT prevent this.  "
                "Recommended fix: in fam/utils/export.py, prepend "
                "a tab or single-quote to any cell value whose "
                "first char is =, +, -, @, \\t.  Also apply the "
                "same escape in fam/sync/gsheets.py:_cell_value() "
                "since the same payload syncs to Google Sheets "
                "where it WILL execute server-side.  See OWASP "
                "CSV Injection guidance.")

    def test_g2_customer_label_with_equals_prefix(
            self, nightmare_db, tmp_path):
        """Customer label starts with ``=`` — does it leak through
        the Detailed Ledger CSV?"""
        from fam.sync.data_collector import _collect_detailed_ledger
        from fam.utils.export import export_detailed_ledger
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        try:
            order_id, _ = create_customer_order(
                market_day_id=1, customer_label='=HYPERLINK("evil")')
        except Exception:
            return  # validated upstream — good
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=500,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
        _confirm(nightmare_db, tid,
                 [_li(2, 'Cash', 0.0, 500, 0, 500)])
        rows = _collect_detailed_ledger(nightmare_db, 1)
        out = str(tmp_path / 'l.csv')
        export_detailed_ledger(rows, out)
        risky = self._scan_cells_for_formula_injection(out)
        if risky:
            details = "\n  ".join(
                f"row {r}, col '{c}': {v!r}" for r, c, v in risky)
            pytest.fail(
                "SECURITY FINDING (CSV/formula injection via "
                f"customer label):\n  {details}\n"
                "Same defect class as g1.")

    def test_g3_notes_field_with_minus_prefix(
            self, nightmare_db, tmp_path):
        """Notes field starting with ``-`` (negation prefix Excel
        treats as formula start)."""
        from fam.sync.data_collector import _collect_detailed_ledger
        from fam.utils.export import export_detailed_ledger
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-G3')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=500,
            customer_order_id=order_id,
            notes="-2+5+CMD()",
            market_day_date='2026-04-29')
        _confirm(nightmare_db, tid,
                 [_li(2, 'Cash', 0.0, 500, 0, 500)])
        rows = _collect_detailed_ledger(nightmare_db, 1)
        out = str(tmp_path / 'l3.csv')
        export_detailed_ledger(rows, out)
        risky = self._scan_cells_for_formula_injection(out)
        if risky:
            details = "\n  ".join(
                f"row {r}, col '{c}': {v!r}" for r, c, v in risky)
            pytest.fail(
                "SECURITY FINDING (CSV/formula injection via "
                f"notes):\n  {details}")


# ════════════════════════════════════════════════════════════════════
# CATEGORY H: FMNP check-splitting boundary cases
# ════════════════════════════════════════════════════════════════════

class TestCategoryH_FMNPSplitting:
    """Survey finding: data_collector.py splits fmnp_entries into
    per-check rows via integer division.  Boundary cases tested:"""

    def test_h1_zero_check_count_does_not_crash(self, nightmare_db):
        """check_count=0 would cause division-by-zero.  Schema
        and model should refuse — or the splitter must guard."""
        from fam.models.fmnp import create_fmnp_entry
        try:
            entry_id = create_fmnp_entry(
                market_day_id=1, vendor_id=1,
                amount=500, check_count=0, entered_by='T')
            # If creation succeeds, attempt the sync split
            from fam.sync.data_collector import _collect_fmnp_entries
            try:
                rows = _collect_fmnp_entries(nightmare_db, 1)
                # If we got here without crash, splitter handled
                # check_count=0 gracefully.
            except ZeroDivisionError:
                pytest.fail(
                    "FINDING: fmnp_entries with check_count=0 "
                    "crashes the sync data collector with "
                    "ZeroDivisionError.  Defense: refuse "
                    "check_count<=0 at the model layer, OR guard "
                    "the splitter.")
        except (ValueError, sqlite3.IntegrityError):
            pass  # rejection at model is the correct behavior

    def test_h2_check_count_exceeds_amount_in_cents(
            self, nightmare_db):
        """7 checks for $0.05 (5 cents).  Each check should be
        ≥1 cent; remainder distributed.  Sum must equal $0.05."""
        from fam.models.fmnp import create_fmnp_entry
        try:
            entry_id = create_fmnp_entry(
                market_day_id=1, vendor_id=1,
                amount=5, check_count=7, entered_by='T')
            from fam.sync.data_collector import _collect_fmnp_entries
            rows = _collect_fmnp_entries(nightmare_db, 1)
            # Find this entry's split rows
            # Split should produce 5 checks of 1¢ + 2 checks of 0¢
            # That's mathematically right but produces $0.00 rows.
            check_amounts_cents = [
                round(float(r.get('Check Amount', 0)) * 100)
                for r in rows
                if r.get('Entry ID') == entry_id
            ]
            if check_amounts_cents:
                assert sum(check_amounts_cents) == 5
                if 0 in check_amounts_cents:
                    pytest.fail(
                        "FINDING: FMNP split produces $0.00 check "
                        "rows in CSV when check_count > amount in "
                        "cents.  Sum reconciles, but a $0.00 check "
                        "is meaningless and may confuse FAM "
                        "reimbursement workflow.")
        except (ValueError, sqlite3.IntegrityError):
            pass  # rejection acceptable

    def test_h3_split_sum_equals_amount_no_drift(self, nightmare_db):
        """Split a $13.37 amount across 7 checks.  Sum must equal
        $13.37 exactly with NO drift."""
        from fam.models.fmnp import create_fmnp_entry
        entry_id = create_fmnp_entry(
            market_day_id=1, vendor_id=1,
            amount=1337, check_count=7, entered_by='T')
        from fam.sync.data_collector import _collect_fmnp_entries
        rows = _collect_fmnp_entries(nightmare_db, 1)
        check_amts = [
            round(float(r.get('Check Amount', 0)) * 100)
            for r in rows
            if r.get('Entry ID') == entry_id
        ]
        if check_amts:
            assert sum(check_amts) == 1337, (
                f"I_check: sum(per-check amounts) = {sum(check_amts)} "
                f"!= 1337 (amount)")


# ════════════════════════════════════════════════════════════════════
# CATEGORY I: Returning customer cross-market (label collision)
# ════════════════════════════════════════════════════════════════════

class TestCategoryI_CustomerLabelCollision:

    def test_i1_same_label_two_market_days_independent_caps(
            self, nightmare_db):
        """Same customer label on two different market_days must
        track caps independently."""
        from fam.models.customer_order import (
            create_customer_order, get_customer_prior_match,
        )
        from fam.models.transaction import create_transaction
        # Add a second open market day
        nightmare_db.execute(
            "INSERT INTO market_days (id, market_id, date, status, "
            " opened_by) VALUES (2, 1, '2026-04-30', 'Open', 'T')")
        nightmare_db.commit()
        for md in (1, 2):
            ord_id, _ = create_customer_order(
                market_day_id=md, customer_label='C-I1')
            tid, _ = create_transaction(
                market_day_id=md, vendor_id=1, receipt_total=20000,
                customer_order_id=ord_id,
                market_day_date='2026-04-29')
            _confirm(nightmare_db, tid,
                     [_li(1, 'SNAP', 100.0, 20000, 10000, 10000)])
        m1 = get_customer_prior_match('C-I1', 1)
        m2 = get_customer_prior_match('C-I1', 2)
        # Each day independent → $100 each, not $200 combined
        assert m1 == 10000
        assert m2 == 10000


# ════════════════════════════════════════════════════════════════════
# CATEGORY J: Same customer + same vendor multi-txn
# ════════════════════════════════════════════════════════════════════

class TestCategoryJ_SameVendorMultiTxn:

    def test_j1_three_txns_to_same_vendor_in_one_order(
            self, nightmare_db):
        """One customer order, three transactions all to vendor 1.
        prior_match must sum across all three (not double-count)."""
        from fam.models.customer_order import (
            create_customer_order, get_customer_prior_match,
        )
        from fam.models.transaction import create_transaction
        ord_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-J1')
        for _ in range(3):
            tid, _ = create_transaction(
                market_day_id=1, vendor_id=1, receipt_total=2000,
                customer_order_id=ord_id,
                market_day_date='2026-04-29')
            _confirm(nightmare_db, tid,
                     [_li(1, 'SNAP', 100.0, 2000, 1000, 1000)])
        # 3 txns × $10 match each = $30 total
        assert get_customer_prior_match('C-J1', 1) == 3000
        # Reports must still tie
        assert_reports_match_db(nightmare_db, 1)


# ════════════════════════════════════════════════════════════════════
# CATEGORY K: Round-trip persistence drift
# ════════════════════════════════════════════════════════════════════

class TestCategoryK_RoundTripDrift:

    def test_k1_save_reload_resave_no_drift_50_iterations(
            self, nightmare_db):
        """Save → reload payment line items → save the reloaded
        items → repeat 50 times.  Final state must equal the
        first-save state exactly."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
            get_payment_line_items,
        )
        ord_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-K1')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=12345,
            customer_order_id=ord_id,
            market_day_date='2026-04-29')
        # 50% match — fractional cent.
        items = [_li(3, 'Food RX', 50.0, 12345, 4115, 8230)]
        _confirm(nightmare_db, tid, items)
        first = sorted(
            (r['method_amount'], r['match_amount'], r['customer_charged'])
            for r in get_payment_line_items(tid))
        for _ in range(50):
            cur = get_payment_line_items(tid)
            save_payment_line_items(
                tid,
                [{
                    'payment_method_id': r['payment_method_id'],
                    'method_name_snapshot': r['method_name_snapshot'],
                    'match_percent_snapshot': r['match_percent_snapshot'],
                    'method_amount': r['method_amount'],
                    'match_amount': r['match_amount'],
                    'customer_charged': r['customer_charged'],
                } for r in cur])
        last = sorted(
            (r['method_amount'], r['match_amount'], r['customer_charged'])
            for r in get_payment_line_items(tid))
        assert first == last, f"Drift after 50 round trips: {first} -> {last}"


# ════════════════════════════════════════════════════════════════════
# CATEGORY L: Sync ↔ DB after void/adjust
# ════════════════════════════════════════════════════════════════════

class TestCategoryL_SyncAfterMutation:

    def test_l1_voided_excluded_from_financial_sheets(
            self, nightmare_db):
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import (
            create_transaction, void_transaction,
        )
        ord_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-L1')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=5000,
            customer_order_id=ord_id,
            market_day_date='2026-04-29')
        _confirm(nightmare_db, tid,
                 [_li(1, 'SNAP', 100.0, 5000, 2500, 2500)])
        before_total = sum(
            r['Total Due to Vendor']
            for r in
            __import__('fam.sync.data_collector', fromlist=['_'])
            ._collect_vendor_reimbursement(nightmare_db, [1])
        )
        void_transaction(tid, voided_by='T')
        after_total = sum(
            r['Total Due to Vendor']
            for r in
            __import__('fam.sync.data_collector', fromlist=['_'])
            ._collect_vendor_reimbursement(nightmare_db, [1])
        )
        assert round((before_total - after_total) * 100) == 5000


# ════════════════════════════════════════════════════════════════════
# CATEGORY M: DB triggers under malicious direct INSERTs
# ════════════════════════════════════════════════════════════════════

class TestCategoryM_DBTriggers:

    def test_m1_negative_method_amount_rejected(self, nightmare_db):
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        ord_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-M1')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            customer_order_id=ord_id,
            market_day_date='2026-04-29')
        with pytest.raises(sqlite3.IntegrityError):
            nightmare_db.execute("""
                INSERT INTO payment_line_items
                (transaction_id, payment_method_id,
                 method_name_snapshot, match_percent_snapshot,
                 method_amount, match_amount, customer_charged)
                VALUES (?, 1, 'SNAP', 100.0, -100, 0, -100)
            """, (tid,))

    def test_m2_per_line_invariant_blocked_by_db(
            self, nightmare_db):
        """v1.9.10 (Finding H-3 fix): the schema v28 trigger
        ``chk_pli_invariant_insert`` rejects any direct INSERT
        that violates ``customer_charged + match_amount =
        method_amount``.  Defense-in-depth on top of the
        application engine's invariant maintenance.
        """
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        ord_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-M2')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            customer_order_id=ord_id,
            market_day_date='2026-04-29')
        # Violates I1: customer 100 + match 200 = 300 ≠ method 1000
        with pytest.raises(sqlite3.IntegrityError,
                           match="customer_charged"):
            nightmare_db.execute("""
                INSERT INTO payment_line_items
                (transaction_id, payment_method_id,
                 method_name_snapshot, match_percent_snapshot,
                 method_amount, match_amount, customer_charged)
                VALUES (?, 1, 'SNAP', 100.0, 1000, 200, 100)
            """, (tid,))

    def test_m3_per_line_invariant_blocked_on_update(
            self, nightmare_db):
        """v1.9.10: the v28 update trigger also rejects writes
        that would BREAK the invariant on an existing row."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        ord_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-M3')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            customer_order_id=ord_id,
            market_day_date='2026-04-29')
        _confirm(nightmare_db, tid,
                 [_li(1, 'SNAP', 100.0, 1000, 500, 500)])
        # Try to UPDATE a row to violating values
        with pytest.raises(sqlite3.IntegrityError,
                           match="customer_charged"):
            nightmare_db.execute("""
                UPDATE payment_line_items
                SET match_amount = 999
                WHERE transaction_id = ?
            """, (tid,))


# ════════════════════════════════════════════════════════════════════
# CATEGORY N: Mutation testing — vary each scenario
# ════════════════════════════════════════════════════════════════════

class TestCategoryN_Mutations:
    """For the highest-risk scenarios, run quick variations to
    confirm robustness across nearby parameter values."""

    @pytest.mark.parametrize('receipt', [1, 99, 100, 101,
                                          9999, 10000, 10001,
                                          99999, 100000])
    def test_n1_invariant_holds_across_receipt_sizes(
            self, nightmare_db, receipt):
        """SNAP @ 100% across 9 receipt sizes from $0.01 to $1000."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        ord_id, _ = create_customer_order(
            market_day_id=1, customer_label=f'C-N1-{receipt}')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=receipt,
            customer_order_id=ord_id,
            market_day_date='2026-04-29')
        # Use breakdown engine for proper rounding.
        bd = calculate_payment_breakdown(
            receipt,
            [{'method_amount': receipt, 'match_percent': 100.0}],
            match_limit=None)
        assert bd['is_valid']
        li = bd['line_items'][0]
        items = [_li(1, 'SNAP', 100.0, li['method_amount'],
                      li['match_amount'], li['customer_charged'])]
        _confirm(nightmare_db, tid, items)
        assert_per_line_invariant(nightmare_db, tid)
        assert_txn_reconciles(nightmare_db, tid)

    @pytest.mark.parametrize('match_pct',
                              [0.0, 0.5, 25.0, 33.33, 50.0,
                               100.0, 150.0, 200.0, 999.0])
    def test_n2_invariant_holds_across_match_pcts(
            self, nightmare_db, match_pct):
        """Same $100 receipt across 9 match percentages."""
        receipt = 10000
        bd = calculate_payment_breakdown(
            receipt,
            [{'method_amount': receipt, 'match_percent': match_pct}],
            match_limit=None)
        assert bd['is_valid'], (
            f"breakdown invalid at match_pct={match_pct}: {bd['errors']}")
        li = bd['line_items'][0]
        # Per-line invariant must hold
        assert li['customer_charged'] + li['match_amount'] == li['method_amount']
        # method_amount equals receipt
        assert li['method_amount'] == receipt

    @pytest.mark.parametrize('cap', [0, 1, 99, 100, 9999, 10000,
                                       100000, None])
    def test_n3_cap_boundaries(self, nightmare_db, cap):
        """Match cap stress across boundary values."""
        bd = calculate_payment_breakdown(
            10000,
            [{'method_amount': 10000, 'match_percent': 100.0}],
            match_limit=cap)
        assert bd['is_valid']
        li = bd['line_items'][0]
        assert li['customer_charged'] + li['match_amount'] == 10000
        if cap is not None:
            assert bd['fam_subsidy_total'] <= cap, (
                f"cap={cap} but subsidy={bd['fam_subsidy_total']}")
