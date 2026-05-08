"""Auto-Distribute respects per-vendor payment-method eligibility
(v2.0.7 fix, user-reported 2026-05-06).

Reproducer (from the user's screenshot):

  Order with three vendors:
    1.11 Juice Bar    $14.52   ❌ SNAP-ineligible
    Fudgie Wudgie     $25.42   ✓ SNAP eligible
    Healthy Heartbeets $36.52  ✓ SNAP eligible

  User clicks **Auto-Distribute**.  Pre-fix: a single SNAP row appears
  with Charge=$38.23, Match=$38.23, Total=$76.46 — silently
  attributing 1.11 Juice Bar's $14.52 share via SNAP.  The Vendor
  Breakdown grid correctly showed the ❌ for SNAP, but the engine
  ignored that constraint when picking the overflow method.

Root cause: ``PaymentScreen._add_overflow_row`` chose the highest-
match-percent method available at the market without intersecting
with per-vendor eligibility (the ``vendor_payment_methods`` junction
introduced in schema v24).  The manual-add path's ``_refresh_method_choices``
already filtered correctly; only the auto-add path was lax.

Fix: ``_add_overflow_row`` now filters candidates to the intersection
of methods that EVERY vendor on the current order is registered for.
"""

import sqlite3

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


# ──────────────────────────────────────────────────────────────────
# Fixture: market + 3 vendors with mixed SNAP eligibility
# ──────────────────────────────────────────────────────────────────
@pytest.fixture
def db_with_order(tmp_path):
    """Mirror the user's reported scenario: 3-vendor order, one
    vendor is SNAP-ineligible.  Cash is universally eligible."""
    db_file = str(tmp_path / "test_autodist_eligibility.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()

    # Market
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES "
        "(1, 'New Test Market', 10000, 1)")

    # Three vendors mirroring the screenshot
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES "
        "(1, '1.11 Juice Bar'), "
        "(2, 'Fudgie Wudgie'), "
        "(3, 'Healthy Heartbeets')")

    # SNAP (100% match, non-denom), Cash (0% match, non-denom)
    conn.execute(
        "INSERT INTO payment_methods "
        "(id, name, match_percent, is_active, sort_order) VALUES "
        "(1, 'SNAP', 100.0, 1, 1), "
        "(2, 'Cash', 0.0, 1, 2)")

    # All vendors at this market, both methods at this market
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, payment_method_id) "
        "VALUES (1, 1), (1, 2)")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) VALUES "
        "(1, 1), (1, 2), (1, 3)")

    # **The key data**: 1.11 Juice Bar (id=1) does NOT accept SNAP.
    # Fudgie Wudgie (id=2) and Healthy Heartbeets (id=3) accept both.
    # All three accept Cash.
    conn.execute(
        "INSERT INTO vendor_payment_methods "
        "(vendor_id, payment_method_id) VALUES "
        "(1, 2), "                  # 1.11 Juice Bar: Cash only
        "(2, 1), (2, 2), "          # Fudgie Wudgie:  SNAP + Cash
        "(3, 1), (3, 2)")           # Healthy:        SNAP + Cash

    # Open market day
    conn.execute(
        "INSERT INTO market_days "
        "(id, market_id, date, status, opened_by) "
        "VALUES (1, 1, '2026-05-06', 'Open', 'Tester')")
    conn.commit()
    yield conn
    close_connection()


def _create_three_vendor_order(conn):
    """Create the user's exact reproducer order: $14.52 / $25.42 /
    $36.52 across the three vendors.  Returns order_id."""
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction

    order_id, _ = create_customer_order(market_day_id=1)
    for vendor_id, receipt_cents in [
        (1, 1452),   # 1.11 Juice Bar: $14.52
        (2, 2542),   # Fudgie Wudgie:  $25.42
        (3, 3652),   # Healthy:         $36.52
    ]:
        create_transaction(
            market_day_id=1,
            vendor_id=vendor_id,
            receipt_total=receipt_cents,
            market_day_date='2026-05-06',
            customer_order_id=order_id,
        )
    return order_id


# ──────────────────────────────────────────────────────────────────
# Direct unit test of the helper
# ──────────────────────────────────────────────────────────────────


class TestUniversallyEligibleHelper:
    """``_compute_universally_eligible_method_ids`` is the engine
    of the fix — it returns the intersection of per-vendor
    eligibility across all order vendors."""

    def test_intersection_excludes_method_one_vendor_rejects(
            self, qtbot, db_with_order):
        from fam.ui.payment_screen import PaymentScreen

        order_id = _create_three_vendor_order(db_with_order)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        eligible = screen._compute_universally_eligible_method_ids()
        # SNAP (id=1) is rejected by 1.11 Juice Bar; intersection
        # MUST exclude it.  Cash (id=2) is accepted by all three;
        # intersection MUST include it.
        assert 1 not in eligible, (
            f"SNAP (id=1) should NOT be in the universally-eligible "
            f"set because 1.11 Juice Bar rejects it.  Got: "
            f"{eligible}")
        assert 2 in eligible, (
            f"Cash (id=2) should be universally eligible.  Got: "
            f"{eligible}")

    def test_returns_none_when_no_order_loaded(
            self, qtbot, db_with_order):
        from fam.ui.payment_screen import PaymentScreen

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        # No order loaded
        assert screen._compute_universally_eligible_method_ids() is None


# ──────────────────────────────────────────────────────────────────
# End-to-end: Auto-Distribute on the user's exact scenario
# ──────────────────────────────────────────────────────────────────


def _select_method_in_row(row, method_name: str) -> bool:
    """Pick a method by name from the row's combo.  Returns True
    if found and selected."""
    combo = row.method_combo
    for i in range(combo.count()):
        data = combo.itemData(i)
        if data and data.get('name') == method_name:
            combo.setCurrentIndex(i)
            return True
    return False


class TestAutoDistributeRespectsEligibility:

    def test_user_reported_scenario_warning_blocks_distribute(
            self, qtbot, db_with_order, monkeypatch):
        """The exact scenario from the bug report: volunteer
        manually picks SNAP, then clicks Auto-Distribute on a
        3-vendor order where 1.11 Juice Bar is SNAP-ineligible.
        Pre-fix: Auto-Distribute happily filled the SNAP row's
        charge with $38.23, silently attributing $14.52 of Juice
        Bar's share via SNAP.  Post-fix: a warning fires naming
        the ineligible vendor, and the row's charge stays $0
        until the volunteer fixes the configuration."""
        from fam.ui.payment_screen import PaymentScreen
        from PySide6.QtWidgets import QMessageBox

        order_id = _create_three_vendor_order(db_with_order)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Volunteer manually picks SNAP on the initial empty row.
        assert len(screen._payment_rows) >= 1
        row0 = screen._payment_rows[0]
        assert _select_method_in_row(row0, 'SNAP'), (
            "fixture issue — couldn't pick SNAP")

        # Capture warning dialogs
        warnings_seen: list[tuple] = []
        monkeypatch.setattr(
            QMessageBox, 'warning',
            staticmethod(
                lambda *a, **kw:
                    warnings_seen.append((a[1] if len(a) > 1 else '',
                                           a[2] if len(a) > 2 else ''))
                    or QMessageBox.Ok))

        # Drive Auto-Distribute
        screen._auto_distribute()

        # The blocking warning must fire
        assert any('Auto-Distribute Blocked' in w[0] for w in warnings_seen), (
            f"Auto-Distribute must emit a blocking warning when an "
            f"existing SNAP row violates per-vendor eligibility on a "
            f"multi-vendor order.  Warnings seen: {warnings_seen}")

        # The warning body must name the ineligible vendor explicitly
        body = next(w[1] for w in warnings_seen
                    if 'Auto-Distribute Blocked' in w[0])
        assert '1.11 Juice Bar' in body, (
            f"Warning body must name the SNAP-ineligible vendor so "
            f"the volunteer knows what to fix.  Got: {body!r}")
        assert 'SNAP' in body, (
            f"Warning body must name the offending payment method.")

        # The SNAP row's charge must still be $0 (engine refused
        # to distribute)
        assert row0._get_active_charge() == 0, (
            f"SNAP row's charge must remain $0 because Auto-"
            f"Distribute refused to allocate a SNAP charge that "
            f"would silently cover a SNAP-ineligible vendor.  "
            f"Got: {row0._get_active_charge()} cents.")

    def test_all_vendors_snap_eligible_distributes_normally(
            self, qtbot, db_with_order, monkeypatch):
        """Control test: when EVERY vendor on the order accepts
        SNAP, Auto-Distribute should distribute through the SNAP
        row as before.  The fix must not over-restrict."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        from PySide6.QtWidgets import QMessageBox

        # Two-vendor order using only the SNAP-eligible vendors
        order_id, _ = create_customer_order(market_day_id=1)
        create_transaction(
            market_day_id=1, vendor_id=2,
            receipt_total=2542,
            market_day_date='2026-05-06',
            customer_order_id=order_id,
        )
        create_transaction(
            market_day_id=1, vendor_id=3,
            receipt_total=3652,
            market_day_date='2026-05-06',
            customer_order_id=order_id,
        )

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        row0 = screen._payment_rows[0]
        _select_method_in_row(row0, 'SNAP')

        warnings_seen: list[tuple] = []
        monkeypatch.setattr(
            QMessageBox, 'warning',
            staticmethod(
                lambda *a, **kw:
                    warnings_seen.append((a[1] if len(a) > 1 else '',
                                           a[2] if len(a) > 2 else ''))
                    or QMessageBox.Ok))

        screen._auto_distribute()

        # No blocking warning — both vendors accept SNAP
        assert not any(
            'Auto-Distribute Blocked' in w[0] for w in warnings_seen), (
            f"No warning should fire when SNAP is universally "
            f"eligible.  Got: {warnings_seen}")

        # Engine distributed through the SNAP row
        assert row0._get_active_charge() > 0, (
            f"SNAP row should have received a charge.")

    def test_single_vendor_snap_only_distributes(
            self, qtbot, db_with_order, monkeypatch):
        """Single-vendor order — eligibility check intentionally
        only fires on multi-vendor orders, so single-vendor flows
        are unaffected."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        from PySide6.QtWidgets import QMessageBox

        order_id, _ = create_customer_order(market_day_id=1)
        create_transaction(
            market_day_id=1, vendor_id=2,
            receipt_total=5000,
            market_day_date='2026-05-06',
            customer_order_id=order_id,
        )

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        row0 = screen._payment_rows[0]
        _select_method_in_row(row0, 'SNAP')

        warnings_seen: list[tuple] = []
        monkeypatch.setattr(
            QMessageBox, 'warning',
            staticmethod(
                lambda *a, **kw:
                    warnings_seen.append((a[1] if len(a) > 1 else '',
                                           a[2] if len(a) > 2 else ''))
                    or QMessageBox.Ok))

        screen._auto_distribute()
        assert not any(
            'Auto-Distribute Blocked' in w[0] for w in warnings_seen)
        assert row0._get_active_charge() > 0


# ──────────────────────────────────────────────────────────────────
# Denom-coverage refinement (second-pass fix, 2026-05-06)
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db_with_food_rx(tmp_path):
    """Same shape as db_with_order, but adds Food RX (denominated
    $10 unit, 100% match) eligible only for 1.11 Juice Bar so the
    user can pre-cover the SNAP-ineligible vendor with a denom row."""
    db_file = str(tmp_path / "test_autodist_denom_coverage.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()

    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 10000, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES "
        "(1, '1.11 Juice Bar'), "
        "(2, 'Fudgie Wudgie'), "
        "(3, 'Healthy Heartbeets')")
    # SNAP, Cash, Food RX (denominated $10)
    conn.execute(
        "INSERT INTO payment_methods "
        "(id, name, match_percent, is_active, sort_order) VALUES "
        "(1, 'SNAP', 100.0, 1, 1), "
        "(2, 'Cash', 0.0, 1, 2)")
    conn.execute(
        "INSERT INTO payment_methods "
        "(id, name, match_percent, is_active, sort_order, denomination) "
        "VALUES (3, 'Food RX', 100.0, 1, 3, 1000)")  # $10 unit
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, payment_method_id) "
        "VALUES (1, 1), (1, 2), (1, 3)")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1), (1, 2), (1, 3)")
    # Eligibility:
    #   Juice Bar:        Cash + Food RX (NO SNAP)
    #   Fudgie Wudgie:    SNAP + Cash + Food RX
    #   Healthy Heartbeets: SNAP + Cash + Food RX
    conn.execute(
        "INSERT INTO vendor_payment_methods "
        "(vendor_id, payment_method_id) VALUES "
        "(1, 2), (1, 3), "
        "(2, 1), (2, 2), (2, 3), "
        "(3, 1), (3, 2), (3, 3)")
    conn.execute(
        "INSERT INTO market_days "
        "(id, market_id, date, status, opened_by) "
        "VALUES (1, 1, '2026-05-06', 'Open', 'T')")
    conn.commit()
    yield conn
    close_connection()


class TestDenomCoverageReleasesEligibilityCheck:
    """The user's second-pass report: SNAP row + Food RX row bound
    to the SNAP-ineligible vendor with charge that fully covers
    that vendor's receipt should NOT trigger the warning, because
    SNAP no longer needs to flow to that vendor."""

    def test_full_denom_coverage_of_ineligible_vendor_unblocks_distribute(
            self, qtbot, db_with_food_rx, monkeypatch):
        """Reproducer of the user's second screenshot:
            - 1.11 Juice Bar $14.52 (SNAP-ineligible)
            - Fudgie Wudgie $25.42 (SNAP eligible)
            - Healthy Heartbeets $36.52 (SNAP eligible)
            - SNAP row added (no charge yet)
            - Food RX row added: 2 × $10 = $20 method_amount, $10 charge,
              bound to 1.11 Juice Bar (covers $14.52 with $5.48 forfeit)
            - Click Auto-Distribute
        Pre-fix: warning fires, refusing to distribute.  This is
        wrong because SNAP doesn't need to flow to 1.11 Juice Bar.
        Post-fix: no warning, SNAP gets the residual charge for
        Fudgie + Healthy."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        from PySide6.QtWidgets import QMessageBox

        order_id, _ = create_customer_order(market_day_id=1)
        for vid, amt in [(1, 1452), (2, 2542), (3, 3652)]:
            create_transaction(
                market_day_id=1, vendor_id=vid, receipt_total=amt,
                market_day_date='2026-05-06',
                customer_order_id=order_id)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # First row: SNAP, no charge yet
        row_snap = screen._payment_rows[0]
        assert _select_method_in_row(row_snap, 'SNAP')

        # Second row: Food RX bound to 1.11 Juice Bar with $20 of
        # denom value (2 units, fully covers the $14.52 receipt)
        row_food_rx = screen._add_payment_row()
        assert _select_method_in_row(row_food_rx, 'Food RX')
        # Bind to vendor id=1 (1.11 Juice Bar)
        row_food_rx.set_bound_vendor_id(1)
        # Charge = $10 (customer side of $20 method_amount at 100% match)
        row_food_rx._set_active_charge(1000)
        row_food_rx._recompute()

        warnings_seen: list[tuple] = []
        monkeypatch.setattr(
            QMessageBox, 'warning',
            staticmethod(
                lambda *a, **kw:
                    warnings_seen.append(
                        (a[1] if len(a) > 1 else '',
                         a[2] if len(a) > 2 else ''))
                    or QMessageBox.Ok))

        screen._auto_distribute()

        assert not any(
            'Auto-Distribute Blocked' in w[0] for w in warnings_seen), (
            f"When the SNAP-ineligible vendor (1.11 Juice Bar) is "
            f"already fully covered by a Food RX denom row, SNAP "
            f"only has to flow to Fudgie + Healthy (both SNAP-"
            f"eligible).  No warning should fire.  Got: "
            f"{warnings_seen}")

        # SNAP row should now have a charge (engine distributed
        # to it for the residual)
        assert row_snap._get_active_charge() > 0, (
            "SNAP row should have received a charge after Auto-"
            "Distribute — the engine was unblocked once 1.11 "
            "Juice Bar was confirmed denom-covered.")

    def test_partial_denom_coverage_still_blocks(
            self, qtbot, db_with_food_rx, monkeypatch):
        """Edge case: if the denom row only PARTIALLY covers the
        SNAP-ineligible vendor (e.g. Food RX $10 against $14.52
        receipt), the vendor still has $4.52 needing non-denom
        coverage — and SNAP can't flow there.  Warning still fires."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        from PySide6.QtWidgets import QMessageBox

        order_id, _ = create_customer_order(market_day_id=1)
        for vid, amt in [(1, 1452), (2, 2542), (3, 3652)]:
            create_transaction(
                market_day_id=1, vendor_id=vid, receipt_total=amt,
                market_day_date='2026-05-06',
                customer_order_id=order_id)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row_snap = screen._payment_rows[0]
        _select_method_in_row(row_snap, 'SNAP')

        # Food RX: 1 × $10 = $10 method_amount, charge $5 (partial)
        row_food_rx = screen._add_payment_row()
        _select_method_in_row(row_food_rx, 'Food RX')
        row_food_rx.set_bound_vendor_id(1)
        row_food_rx._set_active_charge(500)  # $5 customer charge → $10 method_amount
        row_food_rx._recompute()

        warnings_seen: list[tuple] = []
        monkeypatch.setattr(
            QMessageBox, 'warning',
            staticmethod(
                lambda *a, **kw:
                    warnings_seen.append(
                        (a[1] if len(a) > 1 else '',
                         a[2] if len(a) > 2 else ''))
                    or QMessageBox.Ok))

        screen._auto_distribute()

        # 1.11 Juice Bar receipt is $14.52, denom covers $10 method_amount,
        # leaving $4.52 needing non-denom — SNAP can't fill that gap.
        assert any(
            'Auto-Distribute Blocked' in w[0] for w in warnings_seen), (
            f"Partial denom coverage of a SNAP-ineligible vendor "
            f"should still trigger the warning — $4.52 of 1.11 "
            f"Juice Bar's receipt would otherwise be silently "
            f"covered by SNAP.  Got: {warnings_seen}")
