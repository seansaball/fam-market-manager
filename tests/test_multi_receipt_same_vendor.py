"""Multi-receipt-per-vendor regression tests.

Captures a v1.9.10 onsite bug (2026-05-01):

  Customer C-001-LB1, $226.10 total.  Volunteer entered TWO
  separate receipts at the receipt-intake screen for the SAME
  vendor (1.11 Juice Bar): $11.11 and $100.00.  This is a routine
  flow when a customer has two paper receipts from the same
  stand.

  On the Payment screen, volunteer added a Food RX row bound to
  1.11 Juice Bar with 6 × $10 = $60 charge ($120 method, but
  capped to $111.11 by the engine to match the vendor's
  combined receipts).

  Bug observed: Layer 2C (per-transaction reconciliation guard)
  refused to confirm with::

    "Over-allocation on 1.11 Juice Bar's receipt: $111.11 of
     payments are being applied to a $11.11 receipt.  Reduce a
     denominated payment bound to 1.11 Juice Bar, or change its
     vendor in the row."

  even though the customer's Food RX legitimately covered both
  Juice Bar receipts ($11.11 + $100 = $111.11).

Root cause
----------
Three places assumed "one transaction per vendor" via a
``vendor_to_txn_idx`` map keyed by vendor_id with first-match-
wins.  When the same vendor appeared in TWO transactions, every
bound denom payment dumped onto the FIRST transaction:

  1. ``PaymentScreen._distribute_and_save_payments`` (Phase 1)
  2. ``PaymentScreen._confirm_payment``'s Layer 2C guard
  3. ``tests/_coherence._simulate_per_txn_alloc`` (auditor)

Fix
---
All three now distribute denom payments across ALL of the bound
vendor's transactions, weighted by per-transaction remaining
receipt-balance — same algorithm Phase 2 uses for non-denom rows.
The save still attributes the payment to the bound vendor; it
just splits across that vendor's receipt rows.

These tests fail before the fix and pass after it.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


# ─── Fixture: 2 vendors, one with two receipts ──────────────────────

@pytest.fixture
def two_receipt_db(tmp_path):
    db_file = str(tmp_path / "two_receipt.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()

    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'Test Market', 100000, 0)")

    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, '1.11 Juice Bar')")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (2, 'Healthy Heartbeets')")
    for vid in (1, 2):
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))

    # SNAP non-denom + Food RX denom $10 (both 100% match).
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " denomination, sort_order, is_active) VALUES "
        "(1, 'SNAP', 100.0, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " denomination, sort_order, is_active) VALUES "
        "(2, 'Food RX', 100.0, 1000, 2, 1)")
    for pm_id in (1, 2):
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, "
            " payment_method_id) VALUES (1, ?)", (pm_id,))
    for vid in (1, 2):
        for pm_id in (1, 2):
            conn.execute(
                "INSERT INTO vendor_payment_methods (vendor_id, "
                " payment_method_id) VALUES (?, ?)", (vid, pm_id))

    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-05-01', 'Open', 'Tester')")
    conn.commit()
    yield conn
    close_connection()


def _create_two_receipt_order(conn):
    """The user's reproduction: 1.11 Juice Bar appears TWICE
    (receipts $11.11 + $100), Healthy Heartbeets once ($55.55).

    Order total: $11.11 + $100.00 + $55.55 = $166.66.
    """
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-001-LB1',
        zip_code='15102')
    receipts = [
        (1, 1111),  # 1.11 Juice Bar      $11.11
        (1, 10000),  # 1.11 Juice Bar     $100.00 (SAME vendor, second receipt)
        (2, 5555),  # Healthy Heartbeets  $55.55
    ]
    for vid, receipt in receipts:
        create_transaction(
            market_day_id=1, vendor_id=vid,
            receipt_total=receipt,
            customer_order_id=order_id,
            market_day_date='2026-05-01')
    return order_id


def _add_food_rx_row(screen, vendor_id, units):
    """Food RX row bound to vendor_id with N units of $10 each."""
    row = screen._add_payment_row()
    combo = row.method_combo
    for i in range(combo.count()):
        if 'food rx' in combo.itemText(i).lower():
            combo.setCurrentIndex(i)
            break
    row.set_bound_vendor_id(vendor_id)
    row._set_active_charge(units * 1000)  # $10 denom
    return row


def _add_snap_row(screen, charge_cents):
    row = screen._add_payment_row()
    combo = row.method_combo
    for i in range(combo.count()):
        if 'snap' in combo.itemText(i).lower():
            combo.setCurrentIndex(i)
            break
    row._set_active_charge(charge_cents)
    return row


# ════════════════════════════════════════════════════════════════════
#  1. The exact onsite reproduction
# ════════════════════════════════════════════════════════════════════

class TestMultiReceiptPerVendor:
    """A vendor with multiple receipts in one order must accept
    bound denom payments distributed across those receipts.
    """

    def test_simulate_per_txn_alloc_splits_across_vendor_receipts(
            self, qtbot, two_receipt_db):
        """The auditor predicts what the save path will do.  When
        Food RX 5 × $10 = $50 charge, $100 method is bound to
        Juice Bar (which has two receipts $11.11 + $100), the
        simulator should split $100 across the two transactions
        weighted by per-txn remaining (initially $11.11 / $100 →
        ~10% / ~90%).

        Pre-fix: ALL $100 dumped on the first transaction (the
        $11.11 receipt), reporting $88.89 over-allocation on
        receipt 1 and $100 under-allocation on receipt 2.

        Order: $11.11 + $100 (both Juice Bar) + $55.55
        (Healthy Heartbeets) = $166.66.  Food RX bound to Juice
        Bar covers $100 method, SNAP $33.33 charge ($66.66
        method) covers the rest distributed across all three
        receipts proportionally.
        """
        from fam.ui.payment_screen import PaymentScreen
        from tests._coherence import _simulate_per_txn_alloc

        order_id = _create_two_receipt_order(two_receipt_db)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Empty the auto-added row.
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        # 5 × $10 Food RX = $50 charge, $100 method (100% match).
        _add_food_rx_row(screen, vendor_id=1, units=5)
        # SNAP $33.33 charge → $66.66 method, fills remaining
        # $66.66 of order ($166.66 - $100).
        _add_snap_row(screen, charge_cents=3333)

        screen._update_summary()
        items = screen._collect_line_items()
        per_txn = _simulate_per_txn_alloc(screen, items)

        # All three transactions should have allocations within
        # ±$1 of their receipt totals.  The KEY check: NEITHER
        # Juice Bar receipt should be wildly over- or
        # under-allocated; the $100 method gets split between them
        # proportionally.
        txns = screen._order_transactions
        for t in txns:
            alloc = per_txn.get(t['id'], 0)
            receipt = t['receipt_total']
            gap = alloc - receipt
            assert abs(gap) <= 100, (
                f"vendor={t['vendor_name']} txn={t['id']} "
                f"receipt={receipt} alloc={alloc} gap={gap}: "
                f"multi-receipt-per-vendor split should keep each "
                f"transaction within $1.00 of its receipt"
            )

    def test_save_distributes_denom_across_vendor_receipts(
            self, qtbot, two_receipt_db):
        """The actual save path must commit denom rows across the
        bound vendor's transactions, not dump everything on the
        first one.

        Result: each saved transaction's
        ``SUM(method_amount) == receipt_total`` (within ±1¢).
        """
        from fam.ui.payment_screen import PaymentScreen
        from fam.models.transaction import get_payment_line_items

        order_id = _create_two_receipt_order(two_receipt_db)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        # Food RX bound to Juice Bar (which has two receipts
        # totalling $111.11): 5 × $10 = $50 charge, $100 method.
        # Combined Juice Bar gap = $111.11 − $100 = $11.11.
        # Healthy Heartbeets gap = $55.55.
        # SNAP charge = $33.33 → $66.66 method (covers both gaps
        # exactly: $11.11 + $55.55 = $66.66).
        _add_food_rx_row(screen, vendor_id=1, units=5)
        _add_snap_row(screen, charge_cents=3333)
        screen._update_summary()

        items = screen._collect_line_items()
        # Confirm sanity: the engine produced no over-allocation.
        from fam.utils.calculations import calculate_payment_breakdown
        entries = [
            {'method_amount': it['method_amount'],
             'match_percent': it['match_percent'],
             'denomination': it.get('denomination')}
            for it in items
        ]
        result = calculate_payment_breakdown(
            screen._order_total, entries,
            match_limit=screen._match_limit)
        assert result['is_valid'], result.get('errors')

        # Drive the save path directly (commit=True, no UI dialog).
        screen._distribute_and_save_payments(
            items, screen._order_total, commit=True)

        # Now read back saved line items per transaction and check
        # SUM(method_amount) == receipt_total for each.
        for t in screen._order_transactions:
            saved = get_payment_line_items(t['id'])
            saved_sum = sum(li['method_amount'] for li in saved)
            assert abs(saved_sum - t['receipt_total']) <= 1, (
                f"vendor={t['vendor_name']} txn={t['id']} "
                f"receipt={t['receipt_total']} saved_sum={saved_sum}: "
                f"save must cover each receipt within ±1¢"
            )

    def test_confirm_guard_does_not_falsely_flag_multi_receipt(
            self, qtbot, two_receipt_db):
        """Layer 2C reconciliation guard MUST NOT block confirm
        when the order has the same vendor on two transactions and
        the customer's bound denom payment legitimately covers
        both receipts combined.

        Pre-fix: guard reported "Over-allocation on Juice Bar's
        receipt: $X is being applied to a $11.11 receipt" because
        every Juice Bar bound denom dumped on the first txn.
        """
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_two_receipt_order(two_receipt_db)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add_food_rx_row(screen, vendor_id=1, units=5)   # $100 method
        _add_snap_row(screen, charge_cents=3333)         # $66.66 method
        screen._update_summary()

        # Trigger confirm path; the guard runs inside
        # _confirm_payment.  We don't actually want to commit —
        # spy on the error_label visibility instead.
        screen._confirm_payment()
        # If the guard fires, it sets error_label.text() to the
        # over-allocation message and re-enables confirm_btn.
        err_text = screen.error_label.text() if hasattr(
            screen, 'error_label') else ''
        assert 'Over-allocation' not in err_text, (
            f"Layer 2C falsely flagged a multi-receipt-per-vendor "
            f"order: {err_text!r}"
        )
        assert 'Juice Bar' not in err_text or '$11.11' not in err_text, (
            "guard should not single out one of the vendor's "
            "receipts when the customer's denom legitimately "
            "covers the combined total"
        )
