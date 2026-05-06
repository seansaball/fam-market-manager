"""Phase 6b: ``resolve_payment_state`` equivalence test.

Before any call site is migrated, prove that ``resolve_payment_state``
produces output IDENTICAL to the existing engine + forfeit chain
across every parametrized scenario in the cross-layer matrix.

This is the safety net for the migration: as long as both runs
produce equivalent output, switching call sites is safe.
"""
import pytest

from tests.test_cross_layer_parity_matrix import SCENARIOS, _drive_payment_screen


@pytest.fixture
def matrix_db(request, tmp_path, monkeypatch):
    """Re-build the cross-layer-matrix fixture for equivalence
    testing."""
    from fam.database.connection import (
        set_db_path, get_connection, close_connection,
    )
    from fam.database.schema import initialize_database
    from fam.models.customer_order import (
        create_customer_order, update_customer_order_status,
    )
    from fam.models.transaction import (
        create_transaction, confirm_transaction,
        save_payment_line_items,
    )
    from PySide6.QtWidgets import QMessageBox

    scenario = request.param
    db_file = str(tmp_path / f"eq_{scenario.name}.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', ?, ?)",
        (scenario.daily_cap_cents,
         1 if scenario.cap_active else 0))
    for vr in scenario.vendors:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vr.vid, vr.name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vr.vid,))
    methods = [
        (1, 'SNAP', 100.0, None, 1),
        (2, 'Cash', 0.0, None, 2),
        (3, 'Food RX', 100.0, 1000, 3),
        (4, 'JH Food Bucks', 100.0, 200, 4),
        (5, 'JH Tokens', 100.0, 100, 5),
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
    for vr in scenario.vendors:
        for mid, *_rest in methods:
            conn.execute(
                "INSERT INTO vendor_payment_methods "
                "(vendor_id, payment_method_id) VALUES (?, ?)",
                (vr.vid, mid))
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-30', 'Open', 'T')")

    if scenario.prior_match_cents > 0:
        prior_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-TEST',
            zip_code='15102')
        m = scenario.prior_match_cents
        pt_id, _ = create_transaction(
            market_day_id=1, vendor_id=scenario.vendors[0].vid,
            receipt_total=m * 2,
            customer_order_id=prior_id,
            market_day_date='2026-04-30')
        save_payment_line_items(pt_id, [
            {'payment_method_id': 1,
             'method_name_snapshot': 'SNAP',
             'match_percent_snapshot': 100.0,
             'method_amount': m * 2, 'match_amount': m,
             'customer_charged': m,
             'photo_path': None, 'photo_source_paths': []}])
        confirm_transaction(pt_id, confirmed_by='T')
        update_customer_order_status(prior_id, 'Confirmed')

    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-TEST',
        zip_code='15102')
    for vr in scenario.vendors:
        create_transaction(
            market_day_id=1, vendor_id=vr.vid,
            receipt_total=vr.receipt_cents,
            customer_order_id=order_id,
            market_day_date='2026-04-30')
    conn.commit()
    monkeypatch.setattr(
        QMessageBox, 'question',
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.No))
    yield conn, scenario, order_id
    close_connection()


@pytest.mark.parametrize(
    'matrix_db', SCENARIOS,
    ids=lambda s: s.name, indirect=True)
class TestResolvePaymentStateEquivalence:
    """For every scenario, ``resolve_payment_state`` must produce
    output identical to the existing ``calculate_payment_breakdown``
    + ``_apply_denomination_forfeit`` chain to the cent."""

    def test_engine_output_identical(self, qtbot, matrix_db):
        from fam.utils.calculations import (
            calculate_payment_breakdown, resolve_payment_state,
        )
        conn, scenario, order_id = matrix_db
        screen = _drive_payment_screen(qtbot, conn, scenario, order_id)
        items_a = screen._collect_line_items()
        if not items_a:
            pytest.skip(f"[{scenario.name}] no items")

        # Path A: existing chain — calculate_payment_breakdown +
        # _apply_denomination_forfeit (with the screen's per-vendor
        # forfeit function).
        items_a_copy = [dict(it) for it in items_a]
        entries_a = [
            {'method_amount': it['method_amount'],
             'match_percent': it['match_percent'],
             'denomination': it.get('denomination')}
            for it in items_a_copy
        ]
        result_a = calculate_payment_breakdown(
            screen._order_total, entries_a,
            match_limit=screen._match_limit)
        overage_a = screen._check_denomination_overage(
            result_a, screen._order_total)
        if overage_a > 0:
            screen._apply_denomination_forfeit(
                result_a, items_a_copy, overage_a)

        # Path B: canonical resolve_payment_state.
        items_b_copy = [dict(it) for it in items_a]
        result_b = resolve_payment_state(
            screen._order_total, items_b_copy,
            match_limit=screen._match_limit,
            apply_denomination_forfeit_fn=(
                screen._apply_denomination_forfeit))

        # Equivalence: line items match.
        assert len(result_a['line_items']) == len(result_b['line_items']), (
            f"[{scenario.name}] line count differs: "
            f"a={len(result_a['line_items'])}, "
            f"b={len(result_b['line_items'])}")
        for i, (la, lb) in enumerate(zip(
                result_a['line_items'], result_b['line_items'])):
            assert la['method_amount'] == lb['method_amount'], (
                f"[{scenario.name}] line[{i}] method: "
                f"a={la['method_amount']}c b={lb['method_amount']}c")
            assert la['match_amount'] == lb['match_amount'], (
                f"[{scenario.name}] line[{i}] match: "
                f"a={la['match_amount']}c b={lb['match_amount']}c")
            assert la['customer_charged'] == lb['customer_charged'], (
                f"[{scenario.name}] line[{i}] customer: "
                f"a={la['customer_charged']}c "
                f"b={lb['customer_charged']}c")

        # Equivalence: aggregates match.
        for key in ('customer_total_paid', 'fam_subsidy_total',
                     'allocated_total', 'allocation_remaining',
                     'match_was_capped'):
            assert result_a[key] == result_b[key], (
                f"[{scenario.name}] {key}: a={result_a[key]} "
                f"b={result_b[key]}")

    def test_items_synced_after_resolve(self, qtbot, matrix_db):
        """resolve_payment_state must mutate items in place to
        reflect the post-cap-aware state.  Each item's
        method_amount / match_amount / customer_charged must
        equal the corresponding result.line_items value."""
        from fam.utils.calculations import resolve_payment_state
        conn, scenario, order_id = matrix_db
        screen = _drive_payment_screen(qtbot, conn, scenario, order_id)
        items = screen._collect_line_items()
        if not items:
            pytest.skip(f"[{scenario.name}] no items")

        result = resolve_payment_state(
            screen._order_total, items,
            match_limit=screen._match_limit,
            apply_denomination_forfeit_fn=(
                screen._apply_denomination_forfeit))

        for i, li in enumerate(result['line_items']):
            assert items[i]['method_amount'] == li['method_amount'], (
                f"[{scenario.name}] item[{i}].method not synced: "
                f"item={items[i]['method_amount']}c "
                f"line_item={li['method_amount']}c")
            assert items[i]['match_amount'] == li['match_amount'], (
                f"[{scenario.name}] item[{i}].match not synced")
            assert (items[i]['customer_charged']
                    == li['customer_charged']), (
                f"[{scenario.name}] item[{i}].customer not synced")
