"""Multi-vendor multi-denomination-overage interaction tests.

Captures a v1.9.9 onsite bug:

  Customer C-006-LB1, $126.56 across 6 vendors.  Volunteer enters
  bound denominated rows with intentional overages on 3 vendors:

    Elfinwild Farms ($52.32 receipt) — 14 × $2 Food Bucks = $56  (overage $3.68)
    Fudgie Wudgie  ($15.23 receipt) —  4 × $2 Food Bucks = $16  (overage $0.77)
    Fungetarian   ($45.23 receipt) — 12 × $2 Food Bucks = $48  (overage $2.77)

  The remaining 3 vendors (1.11 Juice Bar $10.25, Haffey Family
  Farm $1.23, Hello Hummus $2.30) are un-funded.

  Volunteer adds an empty SNAP row and clicks Auto-Distribute,
  expecting SNAP to fill *only* the un-funded vendors ($13.78 total
  method) and leave the bound denom rows alone.

  Bug observed: Auto-Distribute silently clamped Elfinwild from
  14 → 13 Food Bucks, and the receipt no longer reconciled.

Root cause
----------
``PaymentScreen._push_row_limits`` computes the bound-denom row's
order-level cap as::

  legacy_order_remaining = self._order_total - sum(other rows' method_amount)

When *other* bound denom rows are over-allocated, their
``method_amount`` includes the overage that is actually FAM
**forfeit**, not real order-capacity consumption.  Subtracting the
full method_amount under-counts the order capacity left for *this*
row, and the row's max gets clamped below its current charge.
QSpinBox.setMaximum then silently lowers the value.

The fix caps each *other* bound denom row's order-level
contribution at its bound vendor's receipt total — overage above
that is forfeit, not consumption.

These tests fail before the fix and pass after it.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database
from fam.utils.money import dollars_to_cents


# ─── Fixture: 6-vendor market mirroring the user's onsite scenario ───

@pytest.fixture
def six_vendor_db(tmp_path):
    """6 vendors, SNAP + JH Food Bucks ($2 denom, 100% match).

    Receipts intentionally match the user's screenshot for fidelity
    when chasing this bug.
    """
    db_file = str(tmp_path / "multi_overage.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()

    # Match limit set high so it does NOT interfere with this test.
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'Test Market', 100000, 0)")

    # 6 vendors mirroring the user's session.
    vendors = [
        (1, '1.11 Juice Bar'),
        (2, 'Elfinwild Farms'),
        (3, 'Fudgie Wudgie'),
        (4, 'Fungetarian'),
        (5, 'Haffey Family Farm'),
        (6, 'Hello Hummus'),
    ]
    for vid, name in vendors:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))

    # Two methods: SNAP (non-denom, 100% match) + JH Food Bucks
    # ($2 denom, 100% match).
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " denomination, sort_order, is_active) VALUES "
        "(1, 'SNAP', 100.0, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " denomination, sort_order, is_active) VALUES "
        "(4, 'JH Food Bucks', 100.0, 200, 4, 1)")
    for pm_id in (1, 4):
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, "
            " payment_method_id) VALUES (1, ?)", (pm_id,))

    # Vendor eligibility: SNAP for all; Food Bucks for everyone
    # EXCEPT vendor 1 (1.11 Juice Bar) — mirrors the screenshot's
    # red ✗ in the JH Food Bucks column for that vendor.
    for vid in (1, 2, 3, 4, 5, 6):
        conn.execute(
            "INSERT INTO vendor_payment_methods (vendor_id, "
            " payment_method_id) VALUES (?, 1)", (vid,))
    for vid in (2, 3, 4, 5, 6):
        conn.execute(
            "INSERT INTO vendor_payment_methods (vendor_id, "
            " payment_method_id) VALUES (?, 4)", (vid,))

    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-29', 'Open', 'Tester')")
    conn.commit()
    yield conn
    close_connection()


def _create_six_vendor_order(conn):
    """Create the user's exact 6-vendor order ($126.56 total)."""
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-006-LB1',
        zip_code='15102')
    receipts = [
        (1, 1025),  # 1.11 Juice Bar     $10.25
        (2, 5232),  # Elfinwild Farms    $52.32
        (3, 1523),  # Fudgie Wudgie      $15.23
        (4, 4523),  # Fungetarian        $45.23
        (5, 123),   # Haffey Family Farm $ 1.23
        (6, 230),   # Hello Hummus       $ 2.30
    ]
    for vid, receipt in receipts:
        create_transaction(
            market_day_id=1, vendor_id=vid,
            receipt_total=receipt,
            customer_order_id=order_id,
            market_day_date='2026-04-29')
    return order_id


def _add_bound_food_bucks_row(screen, vendor_id, units):
    """Add a Food Bucks row bound to a specific vendor with N units."""
    row = screen._add_payment_row()
    # Select Food Bucks
    combo = row.method_combo
    for i in range(combo.count()):
        if 'food bucks' in combo.itemText(i).lower():
            combo.setCurrentIndex(i)
            break
    # Bind to vendor
    row.set_bound_vendor_id(vendor_id)
    # Set the unit count via the active charge (units × $2 denomination)
    row._set_active_charge(units * 200)
    return row


def _add_snap_row(screen, charge_cents=0):
    row = screen._add_payment_row()
    combo = row.method_combo
    for i in range(combo.count()):
        if 'snap' in combo.itemText(i).lower():
            combo.setCurrentIndex(i)
            break
    if charge_cents > 0:
        row._set_active_charge(charge_cents)
    return row


# ════════════════════════════════════════════════════════════════════
# 1.  Bound-denom rows survive _push_row_limits when other bound denom
#     rows have overages
# ════════════════════════════════════════════════════════════════════

class TestPushRowLimitsRespectsForfeitOnOtherRows:
    """The bug: a bound denom row's max gets clamped below its
    current charge because OTHER bound denom rows' over-allocation
    is double-counted as order-level consumption.

    Expected: each bound denom row's max should reflect *its own*
    vendor's receipt + 1 unit forfeit allowance, regardless of how
    much other bound rows over-allocate at their own vendors.
    """

    def test_user_scenario_three_overages_no_clamp(self, qtbot,
                                                     six_vendor_db):
        """Faithful reproduction of the user's screenshot scenario.

        Volunteer set 14 / 4 / 12 Food Bucks on Elfinwild / Fudgie /
        Fungetarian, all with intentional overages.  After
        _push_row_limits runs, none of those values should be
        clamped down.
        """
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_six_vendor_order(six_vendor_db)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Replace the auto-added empty row with our 3 bound denom
        # rows + 1 SNAP absorber.
        for r in list(screen._payment_rows):
            screen._remove_payment_row(r) if hasattr(
                screen, '_remove_payment_row') else None
        # Some screens auto-add a row on load; clear by removing
        # methods rather than the row itself if removal helper is
        # absent.
        while screen._payment_rows:
            row = screen._payment_rows[0]
            screen.rows_layout.removeWidget(row)
            row.deleteLater()
            screen._payment_rows.remove(row)

        elfin_row = _add_bound_food_bucks_row(
            screen, vendor_id=2, units=14)   # $28 charge, $56 method
        fudgie_row = _add_bound_food_bucks_row(
            screen, vendor_id=3, units=4)    # $ 8 charge, $16 method
        funge_row = _add_bound_food_bucks_row(
            screen, vendor_id=4, units=12)   # $24 charge, $48 method
        snap_row = _add_snap_row(screen, charge_cents=0)

        # Trigger the limit recompute.
        screen._update_summary()

        # ── ASSERT: bound denom row charges are unchanged ─────────
        assert elfin_row._stepper.count() == 14, (
            f"Elfinwild should still have 14 Food Bucks units; "
            f"got {elfin_row._stepper.count()}.  _push_row_limits "
            "is silently clamping the bound denom row because the "
            "OTHER bound denom rows' overages are being double-"
            "counted as order-level consumption.")
        assert fudgie_row._stepper.count() == 4, (
            f"Fudgie should still have 4 Food Bucks units; got "
            f"{fudgie_row._stepper.count()}")
        assert funge_row._stepper.count() == 12, (
            f"Fungetarian should still have 12 Food Bucks units; "
            f"got {funge_row._stepper.count()}")

        # ── ASSERT: each bound denom row's max ≥ current count ────
        # The +1 forfeit allowance should still be granted on top
        # of the per-vendor cap.
        assert elfin_row._stepper._count_spin.maximum() >= 14, (
            f"Elfinwild's stepper max ({elfin_row._stepper._count_spin.maximum()})"
            f" must be ≥ current count (14)")
        assert fudgie_row._stepper._count_spin.maximum() >= 4
        assert funge_row._stepper._count_spin.maximum() >= 12


# ════════════════════════════════════════════════════════════════════
# 2.  Auto-Distribute fills SNAP for un-funded vendors only and
#     leaves bound denom rows untouched
# ════════════════════════════════════════════════════════════════════

class TestAutoDistributeRespectsBoundDenomOverages:

    def test_auto_distribute_does_not_modify_bound_denom_rows(
            self, qtbot, six_vendor_db):
        """User flow: 3 bound denom rows with overages, empty SNAP
        row, click Auto-Distribute.  SNAP should get exactly the
        un-funded receipt total ($13.78 method = $6.89 charge).
        Bound denom rows must NOT be modified.
        """
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_six_vendor_order(six_vendor_db)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Wipe auto-added rows.
        while screen._payment_rows:
            row = screen._payment_rows[0]
            screen.rows_layout.removeWidget(row)
            row.deleteLater()
            screen._payment_rows.remove(row)

        elfin_row = _add_bound_food_bucks_row(screen, 2, 14)
        fudgie_row = _add_bound_food_bucks_row(screen, 3, 4)
        funge_row = _add_bound_food_bucks_row(screen, 4, 12)
        snap_row = _add_snap_row(screen, charge_cents=0)

        # Click Auto-Distribute.
        screen._auto_distribute()

        # Bound denom rows untouched.
        assert elfin_row._stepper.count() == 14, (
            f"Auto-Distribute changed Elfinwild from 14 → "
            f"{elfin_row._stepper.count()}.  Auto-Distribute should "
            "treat bound denom rows as locked.")
        assert fudgie_row._stepper.count() == 4
        assert funge_row._stepper.count() == 12

        # SNAP gets exactly the un-funded receipt total.
        # Un-funded = 1.11 Juice Bar + Haffey + Hello Hummus
        #           = $10.25 + $1.23 + $2.30 = $13.78 method
        # SNAP @ 100%: charge = method/2 = $6.89 = 689¢
        snap_charge = snap_row._get_active_charge()
        assert snap_charge == 689, (
            f"SNAP charge should be exactly $6.89 (689¢) to cover "
            f"the un-funded vendors' $13.78; got {snap_charge}¢.")


# ════════════════════════════════════════════════════════════════════
# 3.  Per-vendor consumption cap math is correct in isolation
# ════════════════════════════════════════════════════════════════════

class TestOrderLevelCapAccountsForForfeit:
    """Direct unit test of the math: when ROW A is bound to
    vendor V_A with overage, ROW B (bound to V_B) computing its
    legacy_order_remaining must NOT subtract A's overage from
    order capacity — A's overage is FAM forfeit, not consumption.
    """

    def test_two_overages_each_row_max_at_least_current(
            self, qtbot, six_vendor_db):
        """Two bound denom rows both over-allocate their bound
        vendors.  Each row's max must be ≥ its current charge."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_six_vendor_order(six_vendor_db)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        while screen._payment_rows:
            row = screen._payment_rows[0]
            screen.rows_layout.removeWidget(row)
            row.deleteLater()
            screen._payment_rows.remove(row)

        # Two overages: Fudgie 4 × $2 = $16 on $15.23 receipt;
        # Fungetarian 12 × $2 = $24 on $45.23 receipt (FB charge $24,
        # method $48 — overage $2.77).
        fudgie_row = _add_bound_food_bucks_row(screen, 3, 4)
        funge_row = _add_bound_food_bucks_row(screen, 4, 12)

        screen._update_summary()

        # Both rows' steppers must allow their current count.
        assert fudgie_row._stepper._count_spin.maximum() >= 4
        assert funge_row._stepper._count_spin.maximum() >= 12

        # Stepper values not silently clamped down.
        assert fudgie_row._stepper.count() == 4
        assert funge_row._stepper.count() == 12
