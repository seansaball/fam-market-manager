"""Auto-Distribute must NOT clear locked denominated rows.

User-reported screenshot scenario (Customer C-001-LB1, 4 vendors,
$310.80, $100 daily match cap):

  Fudgie Wudgie     receipt $40.00
  Healthy Heartbeets receipt $25.30
  Jill's gourmet dips receipt $120.50
  KizzleFoods       receipt $125.00

Volunteer entered:
  - 10 × JH Food Bucks @ Fudgie Wudgie ($20 charge, $40 method)
  - 7  × JH Food Bucks @ Healthy Heartbeets ($14 charge, $28 method
                          — overage $2.70 forfeit)
  - SNAP at $0
  - Cash at $5.55

Volunteer clicks Auto-Distribute, expecting SNAP to fill the
unallocated vendors (Jill's + KizzleFoods).  Bug observed:
**both locked Food Bucks rows got reset to 0.**

Hypothesis: Auto-Distribute's match-cap deficit inflation pushes
SNAP's charge so high that its uncapped method amount exceeds the
order total.  Then `_push_row_limits` runs (called from
`_update_summary` at the end of `_auto_distribute`) and over-counts
SNAP's method contribution when computing each bound denom row's
``legacy_order_remaining``, which clamps the bound denom row's
max_charge to 0 and silently zeroes the spinbox.

Same bug class as the v1.9.10 multi-vendor overage clamp fix, just
triggered by cap-aware inflation instead of simultaneous overages.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def four_vendor_screenshot_db(tmp_path):
    db_file = str(tmp_path / "autodist.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    # $100 daily match cap (matches the screenshot).
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'Test Market', 10000, 1)")
    vendors = [
        (1, 'Fudgie Wudgie'),
        (2, 'Healthy Heartbeets'),
        (3, "Jill's gourmet dips"),
        (4, 'KizzleFoods'),
    ]
    for vid, name in vendors:
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
    # Universal eligibility for simplicity.
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
    for vid, receipt in [(1, 4000), (2, 2530), (3, 12050), (4, 12500)]:
        create_transaction(
            market_day_id=1, vendor_id=vid,
            receipt_total=receipt,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
    conn.commit()
    yield conn, order_id
    close_connection()


def _add_row(screen, method_substring, charge_cents, vendor_id=None):
    row = screen._add_payment_row()
    combo = row.method_combo
    for i in range(combo.count()):
        if method_substring.lower() in combo.itemText(i).lower():
            combo.setCurrentIndex(i)
            break
    if vendor_id is not None:
        row.set_bound_vendor_id(vendor_id)
    if charge_cents > 0:
        row._set_active_charge(charge_cents)
    return row


# ════════════════════════════════════════════════════════════════════
# 1. THE EXACT SCREENSHOT BUG: locked FB rows must not get cleared
# ════════════════════════════════════════════════════════════════════

class TestAutoDistribute_DoesNotClearLockedDenomRows:
    """Locked denominated rows are physical instruments the customer
    handed over.  Auto-Distribute must never silently zero them."""

    def test_screenshot_scenario_fb_rows_survive(
            self, qtbot, four_vendor_screenshot_db):
        from fam.ui.payment_screen import PaymentScreen
        conn, order_id = four_vendor_screenshot_db

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Wipe auto-added blank rows.
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        # Reproduce the screenshot's input state:
        fb_fudgie = _add_row(screen, 'Food Bucks',
                              charge_cents=2000, vendor_id=1)  # 10×$2
        fb_healthy = _add_row(screen, 'Food Bucks',
                               charge_cents=1400, vendor_id=2)  # 7×$2
        snap_row = _add_row(screen, 'SNAP', charge_cents=0)
        cash_row = _add_row(screen, 'Cash', charge_cents=555)
        screen._update_summary()

        # Pre-condition: FB rows hold their values.
        assert fb_fudgie._stepper.count() == 10, (
            "Pre-state: FB Fudgie should have 10 units")
        assert fb_healthy._stepper.count() == 7, (
            "Pre-state: FB Healthy should have 7 units")

        # User clicks Auto-Distribute.
        screen._auto_distribute()

        # ── ASSERTION ─────────────────────────────────────────
        # Locked denominated rows must still hold the units the
        # customer handed over.  Auto-Distribute can fill SNAP/Cash
        # for the un-funded vendors but must not zero physical
        # instruments.
        assert fb_fudgie._stepper.count() == 10, (
            "BUG: Auto-Distribute cleared FB Fudgie from 10 -> "
            f"{fb_fudgie._stepper.count()}.  Locked denominated "
            "rows must never be modified by Auto-Distribute.")
        assert fb_healthy._stepper.count() == 7, (
            "BUG: Auto-Distribute cleared FB Healthy from 7 -> "
            f"{fb_healthy._stepper.count()}")

    def test_after_autodistribute_no_overallocation(
            self, qtbot, four_vendor_screenshot_db):
        """After Auto-Distribute completes, the order must reconcile:
        sum of method amounts == receipt_total ±0¢.  No SNAP row may
        exceed what the cap-aware engine actually saves."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import calculate_payment_breakdown
        conn, order_id = four_vendor_screenshot_db

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add_row(screen, 'Food Bucks', 2000, vendor_id=1)
        _add_row(screen, 'Food Bucks', 1400, vendor_id=2)
        _add_row(screen, 'SNAP', 0)
        _add_row(screen, 'Cash', 555)
        screen._update_summary()
        screen._auto_distribute()

        # Run the engine on the post-Auto-Distribute state.
        items = screen._collect_line_items()
        entries = [{'method_amount': it['method_amount'],
                    'match_percent': it['match_percent']}
                   for it in items]
        result = calculate_payment_breakdown(
            screen._order_total, entries,
            match_limit=screen._match_limit)
        # Apply forfeit if needed.
        overage = result.get('allocated_total', 0) - screen._order_total
        if overage > 0:
            screen._apply_denomination_forfeit(result, items, overage)

        engine_method_total = sum(li['method_amount']
                                    for li in result['line_items'])
        assert engine_method_total == screen._order_total, (
            f"After Auto-Distribute, engine method total "
            f"{engine_method_total}c != order receipt "
            f"{screen._order_total}c (±0¢).  Cross-layer "
            "reconciliation broken.")

    def test_after_autodistribute_per_vendor_zero_remaining(
            self, qtbot, four_vendor_screenshot_db):
        """V1 (UI invariant): every vendor's Remaining cell must be
        $0.00 after a clean Auto-Distribute fill."""
        from fam.ui.payment_screen import PaymentScreen
        conn, order_id = four_vendor_screenshot_db

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add_row(screen, 'Food Bucks', 2000, vendor_id=1)
        _add_row(screen, 'Food Bucks', 1400, vendor_id=2)
        _add_row(screen, 'SNAP', 0)
        _add_row(screen, 'Cash', 555)
        screen._update_summary()
        screen._auto_distribute()

        # Read the vendor breakdown table.
        table = screen.vendor_table
        drifts = []
        for r in range(table.rowCount()):
            name = table.item(r, 0).text() if table.item(r, 0) else ''
            rem_text = (table.item(r, 2).text()
                        if table.item(r, 2) else '$0.00')
            rem_cents = round(float(rem_text.replace('$', '').replace(',', '').strip()) * 100)
            if rem_cents != 0:
                drifts.append((name, rem_cents))
        assert not drifts, (
            f"V1 violated post-Auto-Distribute: vendor breakdown has "
            f"non-zero Remaining: {drifts}")
