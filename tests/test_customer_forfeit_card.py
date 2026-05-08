"""The Customer Forfeit summary card (v2.0.7-final, Option B,
schema v36) is the single user-visible source of truth for
Phase B token-value forfeit on the PaymentScreen.

Background
----------
Pre-Option B the screen had a complex conditional ``allocated``/
``remaining`` card update branched on ``pre_forfeit_remaining
< 0`` — single-vendor over-allocations got post-forfeit cards,
multi-vendor per-vendor imbalances got pre-forfeit cards.  That
branching logic was hard to reason about and routinely shipped
phantom-negative remaining values from FAM match that was
about to be reduced.

Option B replaces the branching with a dedicated **Customer
Forfeit** card and an unconditional post-forfeit display:

  * ``Allocated`` ALWAYS shows the post-forfeit allocation
    (= receipt total when forfeit pass ran successfully).
  * ``Remaining`` is derived from Allocated and never goes
    phantom-negative.
  * ``Customer Forfeit`` is its own card, always visible,
    showing $0.00 when no Phase B forfeit and $X.XX when fired.
  * The legacy ``denom_overage_warning`` label is permanently
    hidden.

This file pins the new card behavior across Phase A only,
Phase B engaged, and no-overage scenarios.

Math identity (always holds post-forfeit):

    Customer Pays + FAM Match     = Allocated = Receipt Total
    Customer Pays + Customer Forfeit = customer's physical handout
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def fresh_db(tmp_path):
    db_file = str(tmp_path / "customer_forfeit_card.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "(1, 'Food RX', 100.0, 1000, 1, 1), "
        "(2, 'FMNP', 100.0, 500, 2, 1), "
        "(3, 'SNAP', 100.0, NULL, 3, 1), "
        "(4, 'Cash', 0.0, NULL, 4, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        "payment_method_id) VALUES (1, 1), (1, 2), (1, 3), (1, 4)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES "
        "(1, 'Fungetarian'), (2, 'Other Vendor')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1), (1, 2)")
    conn.execute(
        "INSERT INTO vendor_payment_methods (vendor_id, "
        "payment_method_id) VALUES "
        "(1, 1), (1, 2), (1, 3), (1, 4), "
        "(2, 1), (2, 2), (2, 3), (2, 4)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES "
        "(1, 1, '2099-05-01', 'Open', 'Tester')")
    conn.commit()
    yield conn
    close_connection()


def _setup_screen_for_order(qtbot, order_id):
    from fam.ui.payment_screen import PaymentScreen
    screen = PaymentScreen()
    qtbot.addWidget(screen)
    screen.load_customer_order(order_id)
    return screen


def _select_method(row, method_name):
    for i in range(row.method_combo.count()):
        data = row.method_combo.itemData(i)
        if data and data.get('name') == method_name:
            row.method_combo.setCurrentIndex(i)
            return
    raise RuntimeError(f"method {method_name!r} not in combo")


def _card_text(screen, key):
    return screen.summary_row.cards[key].value_label.text()


# ──────────────────────────────────────────────────────────────────
# Phase A only — silent
# ──────────────────────────────────────────────────────────────────


class TestCustomerForfeitCardPhaseAOnly:
    """Phase A reduction is silent: customer's token face value
    reaches the vendor in full, FAM just contributes less.
    The Customer Forfeit card MUST show $0.00."""

    def test_fmnp_token_phase_a_only_card_zero(self, qtbot, fresh_db):
        """1 × $5 FMNP token (100% match) on a $9 receipt: Phase
        A reduces match by $1 (from $5 → $4), Phase B does NOT
        fire (token face value preserved at $5).  Customer
        Forfeit card = $0.00."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-100-LB1')
        create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=900,
            customer_order_id=order_id,
            market_day_date='2099-05-01')
        screen = _setup_screen_for_order(qtbot, order_id)
        row = screen._payment_rows[0]
        _select_method(row, 'FMNP')
        row._stepper.setValue(500)  # 1 token = $5 face
        screen._update_summary()

        assert _card_text(screen, 'customer_forfeit') == '$0.00', (
            f"Phase A only — Customer Forfeit card must show "
            f"$0.00.  Got: "
            f"{_card_text(screen, 'customer_forfeit')!r}")
        assert _card_text(screen, 'remaining') == '$0.00'
        assert _card_text(screen, 'allocated') == '$9.00'

    def test_snap_only_no_overage_card_zero(self, qtbot, fresh_db):
        """SNAP-only payment that exactly balances the receipt —
        no denom overage at all.  Customer Forfeit card stays
        $0.00."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-101-LB1')
        create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=2000,
            customer_order_id=order_id,
            market_day_date='2099-05-01')
        screen = _setup_screen_for_order(qtbot, order_id)
        row = screen._payment_rows[0]
        _select_method(row, 'SNAP')
        row._set_active_charge(1000)  # $10 SNAP, $10 match → $20 method
        screen._update_summary()

        assert _card_text(screen, 'customer_forfeit') == '$0.00'


# ──────────────────────────────────────────────────────────────────
# Phase B engaged — visible
# ──────────────────────────────────────────────────────────────────


class TestCustomerForfeitCardPhaseB:
    """Phase B token-value forfeit: customer hands a denomination
    unit larger than the receipt absorbs even after match is
    fully reduced.  Customer Forfeit card MUST show the exact
    forfeit amount."""

    def test_food_rx_under_receipt_phase_b_card_shows_amount(
            self, qtbot, fresh_db):
        """User-reported scenario: 1 Food RX ($10 face, 100%
        match) on a $6.52 receipt.  Phase A consumes all $10 of
        match; Phase B forfeits $3.48 of customer token face
        value.  Customer Forfeit card = $3.48."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-102-LB1')
        create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=652,
            customer_order_id=order_id,
            market_day_date='2099-05-01')
        screen = _setup_screen_for_order(qtbot, order_id)
        row = screen._payment_rows[0]
        _select_method(row, 'Food RX')
        row._stepper.setValue(1000)  # 1 token = $10 face
        screen._update_summary()

        assert _card_text(screen, 'customer_forfeit') == '$3.48', (
            f"Phase B — Customer Forfeit card must show $3.48 "
            f"for the user-reported scenario.  Got: "
            f"{_card_text(screen, 'customer_forfeit')!r}")
        # Math identity post-forfeit: Allocated = Receipt = $6.52
        assert _card_text(screen, 'allocated') == '$6.52'
        assert _card_text(screen, 'remaining') == '$0.00'
        # Customer Pays = $6.52 (effective), FAM Match = $0
        assert _card_text(screen, 'customer_pays') == '$6.52'
        assert _card_text(screen, 'fam_match') == '$0.00'
        # Customer's physical handout = $6.52 + $3.48 = $10 ✓
        # (the $10 face value of the token they handed over)


# ──────────────────────────────────────────────────────────────────
# Card always visible (math identity on screen)
# ──────────────────────────────────────────────────────────────────


class TestCustomerForfeitCardAlwaysVisible:
    """Per Option B, the Customer Forfeit card is always present
    in the summary row (not conditionally added/removed).  This
    keeps the screen layout stable and the user can see at a
    glance that no forfeit has fired yet."""

    def test_card_present_at_screen_init(self, qtbot, fresh_db):
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-103-LB1')
        create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            customer_order_id=order_id,
            market_day_date='2099-05-01')
        screen = _setup_screen_for_order(qtbot, order_id)
        assert 'customer_forfeit' in screen.summary_row.cards, (
            "Customer Forfeit card must be present in summary "
            "row at screen initialization.")

    def test_card_value_zero_before_any_payment(
            self, qtbot, fresh_db):
        """Before the volunteer picks any method, the card shows
        $0.00 (initial state)."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-104-LB1')
        create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            customer_order_id=order_id,
            market_day_date='2099-05-01')
        screen = _setup_screen_for_order(qtbot, order_id)
        # No payment selected — card shows $0.00 default.
        text = _card_text(screen, 'customer_forfeit')
        assert text in ('$0.00', '0.00'), (
            f"Customer Forfeit card must initialize to $0.00.  "
            f"Got: {text!r}")


# ──────────────────────────────────────────────────────────────────
# Legacy warning label permanently hidden
# ──────────────────────────────────────────────────────────────────


class TestLegacyDenomOverageWarningHidden:
    """The pre-Option B ``denom_overage_warning`` QLabel was the
    user-visible signal for denomination forfeit.  Under Option
    B, the Customer Forfeit summary card subsumes it and the
    label MUST stay hidden — no double-surfacing of the same
    information."""

    def test_warning_hidden_on_phase_a_only(self, qtbot, fresh_db):
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-105-LB1')
        create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=900,
            customer_order_id=order_id,
            market_day_date='2099-05-01')
        screen = _setup_screen_for_order(qtbot, order_id)
        row = screen._payment_rows[0]
        _select_method(row, 'FMNP')
        row._stepper.setValue(500)
        screen._update_summary()
        assert (screen.denom_overage_warning.isHidden()
                or screen.denom_overage_warning.text() == '')

    def test_warning_hidden_on_phase_b(self, qtbot, fresh_db):
        """Even when Phase B fires, the legacy warning stays
        hidden — the Customer Forfeit card is the single source
        of truth now."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-106-LB1')
        create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=652,
            customer_order_id=order_id,
            market_day_date='2099-05-01')
        screen = _setup_screen_for_order(qtbot, order_id)
        row = screen._payment_rows[0]
        _select_method(row, 'Food RX')
        row._stepper.setValue(1000)
        screen._update_summary()
        # Card carries the message now; legacy warning stays
        # hidden to avoid duplicate UI signal.
        assert (screen.denom_overage_warning.isHidden()
                or screen.denom_overage_warning.text() == '')
        assert _card_text(screen, 'customer_forfeit') == '$3.48'


# ──────────────────────────────────────────────────────────────────
# Card unconditional update (no branching on remaining sign)
# ──────────────────────────────────────────────────────────────────


class TestUnconditionalPostForfeitCards:
    """Pre-Option B, the ``allocated``/``remaining`` cards
    updated post-forfeit ONLY when ``pre_forfeit_remaining < 0``.
    Under Option B, they update unconditionally — the engine's
    forfeit pass runs first, and the cards always mirror the
    post-forfeit ``result['allocated_total']``."""

    def test_no_phantom_negative_remaining_under_phase_b(
            self, qtbot, fresh_db):
        """The user-reported original incident: prior to
        Option B, this scenario showed Remaining = -$13.48 in
        the summary card (pre-forfeit phantom).  Under Option
        B, Remaining is $0.00 and Customer Forfeit shows the
        $3.48 customer-side loss."""
        from fam.models.customer_order import create_customer_order
        from fam.models.transaction import create_transaction
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-107-LB1')
        create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=652,
            customer_order_id=order_id,
            market_day_date='2099-05-01')
        screen = _setup_screen_for_order(qtbot, order_id)
        row = screen._payment_rows[0]
        _select_method(row, 'Food RX')
        row._stepper.setValue(1000)
        screen._update_summary()

        # CRITICAL: no phantom negative remaining.
        remaining = _card_text(screen, 'remaining')
        assert not remaining.startswith('$-') and remaining != '$-13.48', (
            f"Remaining card must NEVER show a phantom-negative "
            f"value due to about-to-be-forfeited FAM match.  "
            f"Got: {remaining!r} — this was the user's original "
            f"complaint that motivated Option B.")
        assert remaining == '$0.00'
