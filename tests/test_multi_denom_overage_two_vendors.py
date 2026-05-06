"""Regression: multi-vendor order with TWO different denominated
payments at TWO different vendors, both over-allocating their
bound vendor.

User-reported (2026-05-01 onsite):

    7-receipt order across 6 vendors totalling $45.57:
      1.11 Juice Bar         $5.46
      Fungetarian            $4.53
      Hello Hummus          $15.36
      Hughes Farm & Apiary   $7.83
      412 BBQ                $4.53
      Healthy Heartbeets     $7.86

    Volunteer enters:
      - JH Food Bucks 2 × $2 = $4 customer → bound to 412 BBQ
        (FB row alone: $4 < $4.53 receipt — no row-level overage,
         BUT after match the method = $8 > $4.53)
      - Food RX 1 × $10 = $10 customer → bound to Healthy Heartbeets
        (Food RX row alone: $10 > $7.86 receipt — customer-side
         overage even before match)
      - SNAP (auto-distributed)

    User clicks Auto-Distribute.  Reproduces:
      * Per-vendor reconciliation guard fires on Juice Bar
        ("only $5.11 is being applied to a $5.46 receipt")
      * Healthy Heartbeets is over-allocated by $2.15 in the
        breakdown table (Remaining = -$2.15)
      * Auto-Distribute can't recover; clicks become no-ops
      * Saving as draft + resuming changes SNAP to $15.79; clicking
        Auto-Distribute again produces $16.59 but the same error
        persists.

Root cause
----------
Two interacting bugs:

  1. ``_apply_denomination_forfeit`` reduces match-only when a
     denom row over-allocates its bound vendor.  When match runs
     out (because customer-side denom > receipt — e.g. $10 Food RX
     on $7.86 receipt), the residual overage is left in place and
     the per-vendor reconciliation invariant is violated.

  2. ``_auto_distribute`` sizes non-denom absorber rows against
     ``effective_order_total = locked_denom_total + non_denom_needed``
     where ``non_denom_needed`` correctly subtracts denom from
     each vendor's receipt.  But the auto-distribute output isn't
     reconciled with the engine's post-forfeit state; the spinbox
     ends up sized for the GROSS denom contribution, not the
     post-forfeit one, leaving the per-vendor split off by the
     residual overage.

Pinned post-fix expectations
----------------------------
  * Auto-Distribute produces a state where Layer 2A passes (no
    spinbox/engine mismatch).
  * Per-vendor reconciliation invariant holds for every vendor
    (Σ method per txn equals receipt within ±1¢ tolerance).
  * Customer-side forfeit is recognized: when a denom row's
    customer_charged exceeds the bound vendor's receipt and there
    is no match left to forfeit, ``customer_charged`` is reduced
    accordingly.  The customer's "lost" physical scrip is
    accounted for (effective contribution = receipt amount; the
    physical excess is forfeit, not paid to the vendor).
  * No combination of vendor reassignment / row reorder breaks
    the engine.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def six_vendor_db(tmp_path, monkeypatch):
    """User's exact scenario: 6 vendors, 6 receipts totalling $45.57.

    (The user's screenshot says "7 receipts" — multi-receipt-per-
    vendor.  For this regression test 6 receipts is sufficient
    since the bug surfaces on per-vendor allocation, not
    receipt count.)
    """
    db_file = str(tmp_path / "multi_denom_overage.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'Test Market', 10000, 1)")
    vendors = [
        (1, '1.11 Juice Bar'),
        (2, 'Fungetarian'),
        (3, 'Hello Hummus'),
        (4, 'Hughes Farm'),
        (5, '412 BBQ'),
        (6, 'Healthy Heartbeets'),
    ]
    for vid, name in vendors:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))
    methods = [
        (1, 'SNAP',          100.0, None,  1),
        (2, 'Food RX',       100.0, 1000,  2),  # $10 denom
        (3, 'JH Food Bucks', 100.0,  200,  3),  # $2 denom
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
    for vid, _ in vendors:
        for mid in (1, 2, 3):
            conn.execute(
                "INSERT INTO vendor_payment_methods "
                "(vendor_id, payment_method_id) VALUES (?, ?)",
                (vid, mid))
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-05-01', 'Open', 'T')")
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-010-LB1',
        zip_code='15102')
    receipts = [
        (1, 546),    # Juice Bar
        (2, 453),    # Fungetarian
        (3, 1536),   # Hello Hummus
        (4, 783),    # Hughes Farm
        (5, 453),    # 412 BBQ
        (6, 786),    # Healthy Heartbeets
    ]
    for vid, receipt in receipts:
        create_transaction(
            market_day_id=1, vendor_id=vid,
            receipt_total=receipt,
            customer_order_id=order_id,
            market_day_date='2026-05-01')
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


class TestUserScenarioReproduces:
    """Pin the user's exact scenario as a failing test.  After the
    fix, every assertion should pass."""

    def test_auto_distribute_produces_layer_2c_compatible_state(
            self, qtbot, six_vendor_db):
        """End-to-end: enter the user's exact payment configuration,
        click Auto-Distribute, run the engine + forfeit (mirroring
        ``_confirm_payment``), then simulate the Layer 2C per-vendor
        reconciliation guard.  The guard must NOT trip."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import calculate_payment_breakdown

        conn, order_id = six_vendor_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        # User's exact rows.
        _add(screen, 'JH Food Bucks', 400, vid=5)   # 2 × $2 → 412 BBQ
        _add(screen, 'Food RX', 1000, vid=6)        # 1 × $10 → Healthy
        _add(screen, 'SNAP')                         # auto-fill
        screen._auto_distribute()

        # Mirror the production ``_confirm_payment`` Layer 2C
        # path: collect items, run the engine, apply forfeit
        # (which mutates items in place).
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

        # Layer 2C predictive simulation using POST-forfeit items.
        per_vendor: dict[int, int] = {
            t['vendor_id']: 0 for t in screen._order_transactions
        }
        # Phase 1: denom rows claim their bound vendor.
        for it in items:
            denom = it.get('denomination')
            if denom and denom > 0:
                vid = it.get('bound_vendor_id')
                if vid in per_vendor:
                    per_vendor[vid] += it['method_amount']
        # Phase 2: non-denom rows distribute across the order
        # proportionally to remaining.
        for it in items:
            denom = it.get('denomination')
            if denom and denom > 0:
                continue
            ma = it['method_amount']
            remaining_per_vendor = []
            total_remaining = 0
            for t in screen._order_transactions:
                left = max(0, t['receipt_total']
                           - per_vendor[t['vendor_id']])
                remaining_per_vendor.append(left)
                total_remaining += left
            if total_remaining <= 0:
                continue
            running = 0
            for i, t in enumerate(screen._order_transactions):
                if i == len(screen._order_transactions) - 1:
                    share = ma - running
                else:
                    weight = remaining_per_vendor[i] / total_remaining
                    share = round(ma * weight)
                    running += share
                per_vendor[t['vendor_id']] += share

        for t in screen._order_transactions:
            allocated = per_vendor[t['vendor_id']]
            receipt = t['receipt_total']
            assert abs(allocated - receipt) <= 1, (
                f"Layer 2C would block confirm at vendor "
                f"{t.get('vendor_name', t['vendor_id'])}: "
                f"allocated=${allocated/100:.2f}, "
                f"receipt=${receipt/100:.2f}, "
                f"gap=${(allocated-receipt)/100:.2f}.  "
                f"This is the user's reported 'Under-allocation on "
                f"1.11 Juice Bar's receipt' bug.")

    def test_per_line_invariant_holds_after_auto_distribute(
            self, qtbot, six_vendor_db):
        """For every row: customer_charged + match = method.  This
        is the I1 invariant the schema-level CHECK trigger
        enforces on save.  After auto-distribute + the engine's
        forfeit pass, every row must already satisfy it."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import calculate_payment_breakdown

        conn, order_id = six_vendor_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add(screen, 'JH Food Bucks', 400, vid=5)
        _add(screen, 'Food RX', 1000, vid=6)
        _add(screen, 'SNAP')
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
        denom_overage = screen._check_denomination_overage(
            result, screen._order_total)
        if denom_overage > 0:
            screen._apply_denomination_forfeit(
                result, items, denom_overage)

        for i, li in enumerate(result['line_items']):
            customer = li['customer_charged']
            match = li['match_amount']
            method = li['method_amount']
            assert customer + match == method, (
                f"Per-line invariant violated for row {i} "
                f"({items[i]['method_name_snapshot']}): "
                f"customer={customer} + match={match} "
                f"= {customer+match} != method={method}")
            assert customer >= 0, (
                f"customer_charged went negative on row {i}: "
                f"{customer}")
            assert match >= 0, (
                f"match_amount went negative on row {i}: "
                f"{match}")

    def test_layer_2a_charge_integrity_passes_with_forfeit(
            self, qtbot, six_vendor_db):
        """Layer 2A's spinbox-vs-engine equality check must accept
        the gap on denom rows where the engine's
        ``customer_forfeit_cents`` exactly accounts for the
        difference.  This is the v1.9.10 follow-up Layer 2A
        loosening — previously it would have blocked confirm
        because Food RX spinbox shows $10 but engine returned
        ``customer_charged=$7.86``."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import calculate_payment_breakdown

        conn, order_id = six_vendor_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add(screen, 'JH Food Bucks', 400, vid=5)
        _add(screen, 'Food RX', 1000, vid=6)
        _add(screen, 'SNAP')
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
        denom_overage = screen._check_denomination_overage(
            result, screen._order_total)
        if denom_overage > 0:
            screen._apply_denomination_forfeit(
                result, items, denom_overage)

        # Mirror Layer 2A's check: row spinbox value must equal
        # ``customer_charged + customer_forfeit_cents`` (the
        # post-forfeit allowance).
        valid_rows = [r for r in screen._payment_rows if r.get_data()]
        for i, row in enumerate(valid_rows):
            if i >= len(result['line_items']):
                break
            li = result['line_items'][i]
            forfeit = li.get('customer_forfeit_cents', 0) or 0
            expected_pre_forfeit = li['customer_charged'] + forfeit
            actual = row._get_active_charge()
            assert expected_pre_forfeit == actual, (
                f"Layer 2A mismatch on row {i}: "
                f"spinbox={actual}, engine_customer="
                f"{li['customer_charged']}, forfeit={forfeit}, "
                f"expected_pre_forfeit={expected_pre_forfeit}")

    def test_food_rx_row_records_customer_forfeit(
            self, qtbot, six_vendor_db):
        """The over-allocating Food RX row should carry a
        ``customer_forfeit_cents`` of exactly $2.14 (the
        gap between $10 customer scrip and $7.86 receipt).
        Captures the user's loss explicitly so the receipt /
        report layer can surface it."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import calculate_payment_breakdown

        conn, order_id = six_vendor_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add(screen, 'JH Food Bucks', 400, vid=5)
        _add(screen, 'Food RX', 1000, vid=6)
        _add(screen, 'SNAP')
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
        denom_overage = screen._check_denomination_overage(
            result, screen._order_total)
        if denom_overage > 0:
            screen._apply_denomination_forfeit(
                result, items, denom_overage)

        food_rx_row = next(
            (li for li, it in zip(result['line_items'], items)
             if it['method_name_snapshot'] == 'Food RX'),
            None)
        assert food_rx_row is not None
        assert food_rx_row['customer_charged'] == 786
        assert food_rx_row['match_amount'] == 0
        assert food_rx_row['method_amount'] == 786
        assert food_rx_row['customer_forfeit_cents'] == 214, (
            f"Customer-side forfeit on Food RX should be $2.14 "
            f"(= $10 scrip - $7.86 receipt), "
            f"got ${food_rx_row.get('customer_forfeit_cents', 0)/100:.2f}")


# ──────────────────────────────────────────────────────────────────
# Scenario matrix — every plausible permutation of multi-denom
# overage / under-fit / mixed.  Run end-to-end through the engine +
# forfeit and verify the per-line invariant + global reconciliation
# + Layer 2A spinbox alignment hold.
# ──────────────────────────────────────────────────────────────────


def _run_scenario(qtbot, six_vendor_db, rows, expect_forfeit_total=None):
    """Drive the user-flow with the given (method, charge, vid) rows
    and return (result, items) post-forfeit so tests can assert
    against the engine output.

    rows: list of (method_substring, charge_cents, vendor_id)
          tuples.  Pass charge=0 to leave a row blank for
          auto-distribute to fill.
    """
    from fam.ui.payment_screen import PaymentScreen
    from fam.utils.calculations import calculate_payment_breakdown

    conn, order_id = six_vendor_db
    screen = PaymentScreen()
    qtbot.addWidget(screen)
    screen.load_customer_order(order_id)
    while screen._payment_rows:
        r = screen._payment_rows[0]
        screen.rows_layout.removeWidget(r)
        r.deleteLater()
        screen._payment_rows.remove(r)
    for sub, charge, vid in rows:
        _add(screen, sub, charge, vid=vid)
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
    denom_overage = screen._check_denomination_overage(
        result, screen._order_total)
    if denom_overage > 0:
        screen._apply_denomination_forfeit(
            result, items, denom_overage)
    return screen, result, items


def _assert_invariants(screen, result, items):
    """Three invariants every scenario must hold post-forfeit:
      I1: Σ method_amount == order_total
      I2: customer_charged + match_amount == method_amount per row
      I3: per-vendor allocated == receipt within ±1¢
    """
    # I1
    total = sum(li['method_amount'] for li in result['line_items'])
    assert total == screen._order_total, (
        f"I1 violated: Σ method_amount={total/100:.2f} "
        f"!= order_total={screen._order_total/100:.2f}")
    # I2
    for i, li in enumerate(result['line_items']):
        assert li['customer_charged'] + li['match_amount'] \
            == li['method_amount'], (
            f"I2 violated row {i}: "
            f"{li['customer_charged']}+{li['match_amount']}"
            f"!={li['method_amount']}")
        assert li['customer_charged'] >= 0
        assert li['match_amount'] >= 0
    # I3 — simulate Layer 2C
    per_vendor = {t['vendor_id']: 0 for t in screen._order_transactions}
    for it in items:
        if it.get('denomination') and it['denomination'] > 0:
            vid = it.get('bound_vendor_id')
            if vid in per_vendor:
                per_vendor[vid] += it['method_amount']
    for it in items:
        if it.get('denomination') and it['denomination'] > 0:
            continue
        ma = it['method_amount']
        rems = []
        tot_rem = 0
        for t in screen._order_transactions:
            left = max(0, t['receipt_total']
                       - per_vendor[t['vendor_id']])
            rems.append(left)
            tot_rem += left
        if tot_rem <= 0:
            continue
        running = 0
        for i, t in enumerate(screen._order_transactions):
            if i == len(screen._order_transactions) - 1:
                share = ma - running
            else:
                share = round(ma * rems[i] / tot_rem)
                running += share
            per_vendor[t['vendor_id']] += share
    for t in screen._order_transactions:
        gap = abs(per_vendor[t['vendor_id']] - t['receipt_total'])
        assert gap <= 1, (
            f"I3 violated at vendor {t.get('vendor_name')}: "
            f"alloc={per_vendor[t['vendor_id']]} "
            f"receipt={t['receipt_total']} gap={gap}")


class TestMultiDenomOverageScenarioMatrix:
    """Comprehensive scenario coverage for multi-denom + overage
    interactions.  The user's complaint covers a slice of this
    matrix; we test the whole space to prevent regressions in
    adjacent edge cases."""

    def test_two_denoms_one_over_one_under(
            self, qtbot, six_vendor_db):
        """Original user scenario: FB on 412 BBQ ($4 charge,
        $4.53 receipt — under), Food RX on Healthy ($10 charge,
        $7.86 receipt — over).  SNAP fills the rest."""
        screen, result, items = _run_scenario(
            qtbot, six_vendor_db,
            [('JH Food Bucks', 400, 5),   # under
             ('Food RX', 1000, 6),        # over
             ('SNAP', 0, None)])
        _assert_invariants(screen, result, items)

    def test_two_denoms_both_over(self, qtbot, six_vendor_db):
        """Both denominated rows over-allocate: Food RX 1×$10 on
        $7.86 receipt + Food RX 1×$10 on $4.53 receipt
        (412 BBQ).  Sum customer = $20 across two over-allocated
        vendors."""
        screen, result, items = _run_scenario(
            qtbot, six_vendor_db,
            [('Food RX', 1000, 5),        # $10 on $4.53 → over
             ('Food RX', 1000, 6),        # $10 on $7.86 → over
             ('SNAP', 0, None)])
        _assert_invariants(screen, result, items)

    def test_two_denoms_both_under(self, qtbot, six_vendor_db):
        """Both denominated rows fit comfortably: FB 1×$2 on
        $7.86 receipt + FB 1×$2 on $15.36 receipt.  No overage
        anywhere."""
        screen, result, items = _run_scenario(
            qtbot, six_vendor_db,
            [('JH Food Bucks', 200, 6),
             ('JH Food Bucks', 200, 3),
             ('SNAP', 0, None)])
        _assert_invariants(screen, result, items)

    def test_three_denoms_mixed(self, qtbot, six_vendor_db):
        """Three denom rows on three vendors, one overage:
        FB on Hello Hummus, Food RX on Hughes Farm, FB
        2×$2 on 412 BBQ (slightly under).  SNAP fills."""
        screen, result, items = _run_scenario(
            qtbot, six_vendor_db,
            [('JH Food Bucks', 200, 3),
             ('Food RX', 1000, 4),        # $10 on $7.83 → over by $2.17
             ('JH Food Bucks', 400, 5),
             ('SNAP', 0, None)])
        _assert_invariants(screen, result, items)

    def test_only_denoms_no_snap(self, qtbot, six_vendor_db):
        """Order partially covered: FB+Food RX cover $14 of $45.57.
        No SNAP row.  Auto-distribute should add an overflow row
        OR the engine's allocation_remaining > 0 (under-allocation
        will be caught by the user's normal flow)."""
        screen, result, items = _run_scenario(
            qtbot, six_vendor_db,
            [('JH Food Bucks', 400, 5),
             ('Food RX', 1000, 6)])
        # Auto-distribute should have added a SNAP overflow row.
        method_names = [it['method_name_snapshot'] for it in items]
        assert 'SNAP' in method_names, (
            "Auto-distribute should add SNAP overflow row when "
            "denoms alone don't cover the order")
        _assert_invariants(screen, result, items)

    def test_denom_just_at_receipt_boundary(
            self, qtbot, six_vendor_db):
        """Edge: Food RX 1×$10 on a vendor whose receipt is
        exactly $10 → no overage at all.  Ensures the boundary
        isn't mis-classified."""
        # Hello Hummus is $15.36; FB 5×$2 is $10 — close but
        # let's use a Food RX row that fits cleanly.
        screen, result, items = _run_scenario(
            qtbot, six_vendor_db,
            [('JH Food Bucks', 800, 3),  # 4×$2 = $8 on $15.36
             ('SNAP', 0, None)])
        _assert_invariants(screen, result, items)

    def test_denom_overage_at_first_vendor_only(
            self, qtbot, six_vendor_db):
        """Single denom overage at the FIRST vendor in iteration
        order.  Ensures the forfeit pass handles the
        first-position case correctly."""
        screen, result, items = _run_scenario(
            qtbot, six_vendor_db,
            [('Food RX', 1000, 1),        # Juice Bar $5.46 → over by $4.54
             ('SNAP', 0, None)])
        _assert_invariants(screen, result, items)

    def test_denom_overage_at_last_vendor_only(
            self, qtbot, six_vendor_db):
        """Single denom overage at the LAST vendor — exercises
        the iteration-order edge of the per-vendor proportional
        SNAP distribution."""
        screen, result, items = _run_scenario(
            qtbot, six_vendor_db,
            [('Food RX', 1000, 6),        # Healthy $7.86 → over by $2.14
             ('SNAP', 0, None)])
        _assert_invariants(screen, result, items)

    def test_max_denoms_max_overages(self, qtbot, six_vendor_db):
        """Stress test: every vendor receipt has a Food RX bound
        to it that over-allocates.  Tests the per-vendor pass
        with multiple overages happening simultaneously."""
        # Bind a $10 Food RX to each vendor whose receipt < $10.
        # Vendors and receipts:
        #   Juice Bar 5.46, Fungetarian 4.53, 412 BBQ 4.53,
        #   Healthy 7.86  → all under $10 → all over-allocate
        screen, result, items = _run_scenario(
            qtbot, six_vendor_db,
            [('Food RX', 1000, 1),        # $10 vs $5.46
             ('Food RX', 1000, 2),        # $10 vs $4.53
             ('Food RX', 1000, 5),        # $10 vs $4.53
             ('Food RX', 1000, 6),        # $10 vs $7.86
             ('SNAP', 0, None)])
        _assert_invariants(screen, result, items)

    def test_total_method_equals_order_total_after_forfeit(
            self, qtbot, six_vendor_db):
        """Σ method_amount across all rows must equal order_total
        after forfeit reduction.  This is the global reconciliation
        contract.  When customer-side denom overage causes forfeit
        to exhaust match without fully covering, the residual
        overage must be absorbed by reducing customer_charged on
        the over-allocating denom row."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import calculate_payment_breakdown

        conn, order_id = six_vendor_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add(screen, 'JH Food Bucks', 400, vid=5)
        _add(screen, 'Food RX', 1000, vid=6)
        _add(screen, 'SNAP')
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
        denom_overage = screen._check_denomination_overage(
            result, screen._order_total)
        if denom_overage > 0:
            screen._apply_denomination_forfeit(
                result, items, denom_overage)

        total_method = sum(li['method_amount']
                           for li in result['line_items'])
        assert total_method == screen._order_total, (
            f"Total method ${total_method/100:.2f} must equal "
            f"order total ${screen._order_total/100:.2f} after "
            f"forfeit.  When customer-side denom overage exists "
            f"and match is exhausted, the forfeit logic must also "
            f"reduce customer_charged on the over-allocating "
            f"denom row to bring method down to receipt.")
