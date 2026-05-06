"""Regression: returning customer with multi-vendor, multi-denom
order — confirm must NOT brick when denom-row uncapped match
exceeds the customer's remaining daily cap.

User-reported (2026-04-30 onsite, customer C-001-LB1 returning):

    Order $111.10 across 4 vendors:
      Elfinwild $11.11, Fungetarian $22.22,
      Hughes $33.33, Pond Hill $44.44

    Customer had already redeemed $83.31 of FAM match today.
    Remaining cap: $16.69.

    Volunteer enters:
      - 3 × JH Food Bucks ($2 denom)  → Elfinwild
      - 2 × Food RX ($10 denom)       → Hughes
      - SNAP (Auto-Distributed)

    Layer 2A blocks confirm:
      "Payment row mismatch detected and confirmation was blocked.
       The JH Food Bucks input shows $6.00 but the calculated
       charge after applying caps and reconciliation is $10.31."

    Auto-Distribute does NOT recover from the blocked state.

Root cause
----------
Customer's remaining cap = $16.69.  Denom rows alone need $26 of
match (FB $6 + Food RX $20 at 100% match).  ``denom_uncapped > cap``
triggers ``calculate_payment_breakdown``'s fallback path, which did
a naive proportional reduction across ALL rows:

    cap_ratio = match_limit / total_uncapped
    for li in line_items:
        li['match_amount'] = round(li['match_amount'] * cap_ratio)
        li['customer_charged'] = li['method_amount'] - li['match_amount']

That formula computes ``customer_charged = method - reduced_match``
WITHOUT recognizing that denom customer is FIXED at
``unit_count × denomination``.  Result: FB customer = $10.31 (not a
$2 multiple).  Layer 2A correctly blocks the inconsistency, but the
user has no path forward — Auto-Distribute can't reduce a
cap-binding scenario.

Fix
---
1. ``calculate_payment_breakdown`` cap fallback: snap denom
   customer back to its FIXED value, reduce denom match
   proportionally, reduce denom method by the same amount, then
   inflate non-denom rows' method to absorb the receipt-balance
   gap.  Distribute the residual cap budget to non-denom rows
   proportionally.

2. ``_push_row_limits`` cap-aware non-denom max: when denom
   uncapped match alone meets/exceeds the cap, raise the
   non-denom row's max_charge ceiling to fit the engine's true
   output (which absorbs the denom-method shrinkage).  Without
   this bump the cap-write-back path silently clamps the
   spinbox below the engine's value, Layer 2A blocks confirm,
   and Auto-Distribute can't recover.

This test pins the user's exact scenario.  After the fix:
  - Layer 2A passes (all spinboxes match engine output)
  - Engine produces correct denom-multiple customer values
  - SNAP customer fits within its cap-aware spinbox ceiling
  - Per-line invariant holds for every row
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def returning_customer_db(tmp_path, monkeypatch):
    """Customer C-001-LB1 has already redeemed $83.31 of FAM match
    today; opens a new $111.10 order across 4 vendors."""
    db_file = str(tmp_path / "bricked.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    for vid, name in [(1, 'Elfinwild'), (2, 'Fungetarian'),
                       (3, 'Hughes'), (4, 'Pond Hill')]:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))
    methods = [
        (1, 'SNAP',          100.0, None,  1),
        (3, 'Food RX',       100.0, 1000,  3),  # denom $10
        (4, 'JH Food Bucks', 100.0,  200,  4),  # denom $2
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
        for mid in (1, 3, 4):
            conn.execute(
                "INSERT INTO vendor_payment_methods "
                "(vendor_id, payment_method_id) VALUES (?, ?)",
                (vid, mid))
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-30', 'Open', 'T')")
    from fam.models.customer_order import (
        create_customer_order, update_customer_order_status,
    )
    from fam.models.transaction import (
        create_transaction, confirm_transaction,
        save_payment_line_items,
    )
    # Prior order with $83.31 of match consumed.
    prior_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-001-LB1',
        zip_code='15102')
    pt_id, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=16662,
        customer_order_id=prior_id,
        market_day_date='2026-04-30')
    save_payment_line_items(pt_id, [
        {'payment_method_id': 1,
         'method_name_snapshot': 'SNAP',
         'match_percent_snapshot': 100.0,
         'method_amount': 16662, 'match_amount': 8331,
         'customer_charged': 8331,
         'photo_path': None, 'photo_source_paths': []}])
    confirm_transaction(pt_id, confirmed_by='T')
    update_customer_order_status(prior_id, 'Confirmed')

    # The new order under test.
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-001-LB1',
        zip_code='15102')
    for vid, receipt in [(1, 1111), (2, 2222),
                          (3, 3333), (4, 4444)]:
        create_transaction(
            market_day_id=1, vendor_id=vid,
            receipt_total=receipt,
            customer_order_id=order_id,
            market_day_date='2026-04-30')
    conn.commit()
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, 'question',
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.No))
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


class TestEngineFallbackPreservesDenomCustomer:
    """``calculate_payment_breakdown`` cap fallback (when denom
    uncapped match exceeds the cap) must keep each denom row's
    customer_charged at its FIXED value (= unit_count ×
    denomination).  Per-line invariant must hold for every row."""

    def test_engine_fallback_keeps_denom_customer_fixed(self):
        from fam.utils.calculations import calculate_payment_breakdown

        # Receipt $111.10, FB $12 (3 × $2), Food RX $40 (2 × $10),
        # SNAP $66.66.  Cap $16.69 (after $83.31 prior).  Denom
        # uncapped match = $26 > cap → fallback path.
        result = calculate_payment_breakdown(
            receipt_total=11110,
            payment_entries=[
                {'method_amount': 1200,
                 'match_percent': 100.0,
                 'denomination': 200},
                {'method_amount': 4000,
                 'match_percent': 100.0,
                 'denomination': 1000},
                {'method_amount': 6666,
                 'match_percent': 100.0,
                 'denomination': None},
            ],
            match_limit=1669,
        )

        assert result['match_was_capped'] is True
        # Denom customers must equal their fixed values (the
        # unit_count × denomination they were given).
        fb = result['line_items'][0]
        assert fb['customer_charged'] == 600, (
            f"FB Bucks customer must equal $6.00 (= 3 × $2 tokens), "
            f"got ${fb['customer_charged']/100:.2f}.  Pre-fix the "
            f"proportional cap reduction inflated this to a "
            f"non-$2-multiple value, blocking Layer 2A.")
        food_rx = result['line_items'][1]
        assert food_rx['customer_charged'] == 2000, (
            f"Food RX customer must equal $20.00 (= 2 × $10 "
            f"checks), got ${food_rx['customer_charged']/100:.2f}")

        # Per-line invariant must hold for every row.
        for li in result['line_items']:
            invariant = li['customer_charged'] + li['match_amount']
            assert invariant == li['method_amount'], (
                f"Per-line invariant violated: "
                f"customer={li['customer_charged']} + "
                f"match={li['match_amount']} != "
                f"method={li['method_amount']}")

    def test_total_match_does_not_exceed_cap(self):
        from fam.utils.calculations import calculate_payment_breakdown

        result = calculate_payment_breakdown(
            receipt_total=11110,
            payment_entries=[
                {'method_amount': 1200, 'match_percent': 100.0,
                 'denomination': 200},
                {'method_amount': 4000, 'match_percent': 100.0,
                 'denomination': 1000},
                {'method_amount': 6666, 'match_percent': 100.0,
                 'denomination': None},
            ],
            match_limit=1669,
        )
        assert result['fam_subsidy_total'] <= 1669, (
            f"Engine match exceeded the cap: "
            f"${result['fam_subsidy_total']/100:.2f} > $16.69")


class TestBrickedTransactionConfirmsCleanly:
    """End-to-end PaymentScreen flow for the user's exact onsite
    scenario.  Auto-Distribute + cap-aware row limits must produce
    a state where Layer 2A passes and confirm proceeds."""

    def test_auto_distribute_produces_layer_2a_compatible_state(
            self, qtbot, returning_customer_db):
        """Mirrors the ``_confirm_payment`` Layer 2A path: run the
        engine, apply ``_apply_denomination_forfeit``, then verify
        each row's spinbox matches the post-forfeit ``customer_charged``.
        Layer 2A in production runs in this exact order — applying
        forfeit (which triggers the cap-aware Pass 4 give-back)
        before comparing engine output to row state."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import calculate_payment_breakdown

        conn, order_id = returning_customer_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        # Match the user's screenshot.
        _add(screen, 'Food Bucks', 600, vid=1)   # 3 × $2 on Elfinwild
        _add(screen, 'Food RX', 2000, vid=3)     # 2 × $10 on Hughes
        screen._auto_distribute()

        # Cards must reflect the cap-active end state.
        cards = {
            k: screen.summary_row.cards[k].value_label.text()
            for k in ('allocated', 'customer_pays',
                      'fam_match', 'remaining')
        }
        assert cards['fam_match'] == "$16.69", (
            f"FAM Match must equal full remaining cap $16.69, got "
            f"{cards['fam_match']}")
        assert cards['customer_pays'] == "$94.41", (
            f"Customer Pays must equal $94.41 (= $111.10 receipt − "
            f"$16.69 cap), got {cards['customer_pays']}")

        # Run the engine, then apply forfeit + give-back the same
        # way ``_confirm_payment`` does before Layer 2A.
        items = screen._collect_line_items()
        entries = [
            {'method_amount': it['method_amount'],
             'match_percent': it['match_percent'],
             'denomination': it.get('denomination')}
            for it in items
        ]
        result = calculate_payment_breakdown(
            screen._order_total, entries,
            match_limit=screen._match_limit)
        denom_overage = screen._check_denomination_overage(
            result, screen._order_total)
        if denom_overage > 0:
            screen._apply_denomination_forfeit(
                result, items, denom_overage)

        valid_rows = [
            r for r in screen._payment_rows
            if r.get_data() and r.get_data()['method_amount'] > 0
        ]
        for i, row in enumerate(valid_rows):
            engine_charge = result['line_items'][i]['customer_charged']
            row_charge = row._get_active_charge()
            method_name = row.get_selected_method()['name']
            assert engine_charge == row_charge, (
                f"Layer 2A would block confirm: {method_name} "
                f"row shows {row_charge}c but engine wants "
                f"{engine_charge}c after forfeit + give-back.  "
                f"This is the 'bricked transaction' regression — "
                f"Auto-Distribute must produce a state Layer 2A "
                f"can pass.")

    def test_per_line_invariant_holds_after_auto_distribute(
            self, qtbot, returning_customer_db):
        """customer_charged + match_amount must equal method_amount
        for every row after auto-distribute on the bricked
        scenario."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import calculate_payment_breakdown

        conn, order_id = returning_customer_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)
        _add(screen, 'Food Bucks', 600, vid=1)
        _add(screen, 'Food RX', 2000, vid=3)
        screen._auto_distribute()

        items = screen._collect_line_items()
        entries = [
            {'method_amount': it['method_amount'],
             'match_percent': it['match_percent'],
             'denomination': it.get('denomination')}
            for it in items
        ]
        result = calculate_payment_breakdown(
            screen._order_total, entries,
            match_limit=screen._match_limit)
        for li in result['line_items']:
            invariant = li['customer_charged'] + li['match_amount']
            assert invariant == li['method_amount'], (
                f"Per-line invariant violated after auto-distribute: "
                f"customer={li['customer_charged']} + "
                f"match={li['match_amount']} != "
                f"method={li['method_amount']}")

    def test_denom_rows_keep_unit_multiple_charges(
            self, qtbot, returning_customer_db):
        """The user's spinboxes must remain at their entered values
        (= unit_count × denomination) — they handed over physical
        tokens; no engine recomputation should change that."""
        from fam.ui.payment_screen import PaymentScreen

        conn, order_id = returning_customer_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)
        _add(screen, 'Food Bucks', 600, vid=1)
        _add(screen, 'Food RX', 2000, vid=3)
        screen._auto_distribute()

        fb_row = next(r for r in screen._payment_rows
                      if r.get_selected_method()
                      and r.get_selected_method()['name']
                      == 'JH Food Bucks')
        food_rx_row = next(r for r in screen._payment_rows
                           if r.get_selected_method()
                           and r.get_selected_method()['name']
                           == 'Food RX')
        assert fb_row._get_active_charge() == 600, (
            f"FB Bucks spinbox must remain at $6.00 (3 × $2 tokens "
            f"the customer handed over), got "
            f"${fb_row._get_active_charge()/100:.2f}")
        assert food_rx_row._get_active_charge() == 2000, (
            f"Food RX spinbox must remain at $20.00 (2 × $10 "
            f"checks the customer handed over), got "
            f"${food_rx_row._get_active_charge()/100:.2f}")
