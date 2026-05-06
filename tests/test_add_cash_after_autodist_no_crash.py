"""Regression: adding a Cash row after Auto-Distribute must not crash.

User-reported scenario (Customer C-001-LB1, 4 vendors, $310.80,
$100 daily match cap, displayed as v1.9.9):

  Fudgie Wudgie     receipt $40.00
  Healthy Heartbeets receipt $25.30
  Jill's gourmet dips receipt $120.50
  KizzleFoods       receipt $125.00

Reproducer:
  1. Add 2 JH Food Bucks rows bound to Fudgie + Healthy (with one
     row over-allocating its vendor — denomination forfeit case)
  2. Click Auto-Distribute (SNAP fills un-funded vendors)
  3. Click "+ Add Payment Method" → select Cash
  4. CRASH: ``IndexError: list index out of range`` in
     ``_apply_denomination_forfeit``

Root cause
----------
``calculate_payment_breakdown`` produces a ``line_items`` list with
one entry per input ``payment_entry``.  When a row is added with
charge=$0 and method=$0 mid-flow:

  * ``_collect_line_items`` filters ``method_amount > 0`` →
    Cash row excluded → ``items`` length = 3
  * BUT the engine had been called BEFORE the new row was added,
    OR a separate path adds a 0-method line_item for the new row →
    ``result['line_items']`` length = 4

``_apply_denomination_forfeit`` iterates ``result['line_items']``
and accesses ``items[i]`` by the same index — IndexError when
``i`` exceeds ``len(items)``.

The fix bounds-checks every ``items[i]`` access in all three
forfeit passes.  No semantic change for the matched range.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def four_vendor_db(tmp_path):
    db_file = str(tmp_path / "addcash.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    for vid, name in [(1, 'Fudgie'), (2, 'Healthy'),
                       (3, "Jill's"), (4, 'Kizzle')]:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))
    methods = [
        (1, 'SNAP',          100.0, None, 1),
        (2, 'Cash',            0.0, None, 2),
        (3, 'Food RX',        50.0, None, 3),
        (4, 'JH Food Bucks', 100.0,  200, 4),
        (5, 'JH Tokens',     100.0,  500, 5),
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
    for vid in (1, 2, 3, 4):
        for mid in (1, 2, 3, 4, 5):
            conn.execute(
                "INSERT INTO vendor_payment_methods "
                " (vendor_id, payment_method_id) VALUES (?, ?)",
                (vid, mid))
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-29', 'Open', 'T')")
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-001-LB1',
        zip_code='15102')
    for vid, receipt in [(1, 4000), (2, 2530),
                          (3, 12050), (4, 12500)]:
        create_transaction(
            market_day_id=1, vendor_id=vid,
            receipt_total=receipt,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
    conn.commit()
    yield conn, order_id
    close_connection()


def _add(screen, method_sub, charge=0, vid=None):
    row = screen._add_payment_row()
    combo = row.method_combo
    for i in range(combo.count()):
        if method_sub.lower() in combo.itemText(i).lower():
            combo.setCurrentIndex(i)
            break
    if vid is not None:
        row.set_bound_vendor_id(vid)
    if charge > 0:
        row._set_active_charge(charge)
    return row


class TestAddCashAfterAutoDistribute:
    """Regression: adding a Cash row after Auto-Distribute must
    not raise IndexError."""

    def test_screenshot_scenario_no_crash(
            self, qtbot, four_vendor_db):
        from fam.ui.payment_screen import PaymentScreen
        conn, order_id = four_vendor_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        # Reproduce the screenshot's input state.
        _add(screen, 'Food Bucks', 2000, vid=1)  # 10 FB @ Fudgie
        _add(screen, 'Food Bucks', 1400, vid=2)  # 7 FB @ Healthy

        # Step 1: click Auto-Distribute.  Adds a SNAP overflow row.
        screen._auto_distribute()
        assert len(screen._payment_rows) >= 3, (
            "Auto-Distribute should have added a SNAP row")

        # Step 2: click "+ Add Payment Method" then select Cash.
        # This is what the user did right before the crash.
        cash_row = _add(screen, 'Cash')

        # If we got here without raising, the bug is fixed.
        # Sanity: the new Cash row is present and at $0.
        assert cash_row in screen._payment_rows
        assert cash_row._get_active_charge() == 0

    def test_add_cash_with_high_snap_charge_no_crash(
            self, qtbot, four_vendor_db):
        """Variant: SNAP at a high charge (closer to triggering
        Pass 3 penny reconciliation), then add Cash."""
        from fam.ui.payment_screen import PaymentScreen
        conn, order_id = four_vendor_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add(screen, 'Food Bucks', 2000, vid=1)
        _add(screen, 'Food Bucks', 1400, vid=2)
        screen._auto_distribute()
        # Push SNAP higher to potentially trigger Pass 3.
        snap = next((r for r in screen._payment_rows
                     if r.get_selected_method()
                     and r.get_selected_method()['name'] == 'SNAP'), None)
        if snap is not None:
            snap._set_active_charge(15500)

        # Add Cash — must not crash.
        cash_row = _add(screen, 'Cash')
        assert cash_row._get_active_charge() == 0

    def test_add_food_rx_after_autodist_no_crash(
            self, qtbot, four_vendor_db):
        """Variant: add a Food RX row (50% match, non-denom)
        instead of Cash.  Same code path, different match%."""
        from fam.ui.payment_screen import PaymentScreen
        conn, order_id = four_vendor_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add(screen, 'Food Bucks', 2000, vid=1)
        _add(screen, 'Food Bucks', 1400, vid=2)
        screen._auto_distribute()

        rx_row = _add(screen, 'Food RX')
        assert rx_row._get_active_charge() == 0
