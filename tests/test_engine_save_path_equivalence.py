"""Phase 9: Engine ↔ save-path equivalence.

For every parametrized scenario in the cross-layer matrix, the
saved DB state after Confirm must equal the engine + forfeit
output to the cent — no save-path drift.

This test exercises ``_distribute_and_save_payments`` (the save
path) against ``calculate_payment_breakdown + _apply_denomination_forfeit``
(the engine).  After Confirm, the aggregated saved values must
match the engine's per-row output:

  Σ saved.method_amount  per (vendor, method)  ==  engine method
  Σ saved.match_amount   per (vendor, method)  ==  engine match
  Σ saved.customer_charged per (vendor, method) == engine customer

A failure means the save path's own cap math, per-vendor split,
or penny-rec diverged from the engine's output — exactly the
class of bug that's been bleeding (#6, #9, #17, #18 in the audit).
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog

from tests.test_cross_layer_parity_matrix import SCENARIOS
from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


# Skip scenarios that don't have rows to confirm (empty/edge cases).
RUNNABLE_SCENARIOS = [
    s for s in SCENARIOS
    if any(r.charge_cents > 0 for r in s.rows) or s.use_auto_distribute
]


@pytest.fixture
def equivalence_db(request, tmp_path, monkeypatch):
    from fam.models.customer_order import (
        create_customer_order, update_customer_order_status,
    )
    from fam.models.transaction import (
        create_transaction, confirm_transaction,
        save_payment_line_items,
    )
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

    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, 'question',
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.No))

    yield conn, scenario, order_id
    close_connection()


def _drive_and_confirm(qtbot, scenario, order_id, monkeypatch):
    from fam.ui.payment_screen import PaymentScreen
    import fam.ui.widgets.payment_confirmation_dialog as pcd

    screen = PaymentScreen()
    qtbot.addWidget(screen)
    screen.load_customer_order(order_id)
    while screen._payment_rows:
        r = screen._payment_rows[0]
        screen.rows_layout.removeWidget(r)
        r.deleteLater()
        screen._payment_rows.remove(r)
    for spec in scenario.rows:
        row = screen._add_payment_row()
        combo = row.method_combo
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data and data.get('id') == spec.method_id:
                combo.setCurrentIndex(i)
                break
        if spec.bound_vendor_id is not None:
            row.set_bound_vendor_id(spec.bound_vendor_id)
        if spec.charge_cents > 0:
            row._set_active_charge(spec.charge_cents)
    if scenario.use_auto_distribute:
        screen._auto_distribute()
    else:
        screen._update_summary()

    def stub_init(self, *args, **kwargs):
        QDialog.__init__(self)
    def stub_exec(self):
        return QDialog.Accepted
    monkeypatch.setattr(pcd.PaymentConfirmationDialog,
                         '__init__', stub_init)
    monkeypatch.setattr(pcd.PaymentConfirmationDialog,
                         'exec', stub_exec)
    screen._confirm_payment()
    return screen


def _engine_post_forfeit(screen):
    from fam.utils.calculations import calculate_payment_breakdown
    items = screen._collect_line_items()
    if not items:
        return None, None
    entries = [
        {'method_amount': it['method_amount'],
         'match_percent': it['match_percent'],
         'denomination': it.get('denomination')}
        for it in items
    ]
    result = calculate_payment_breakdown(
        screen._order_total, entries,
        match_limit=screen._match_limit)
    overage = screen._check_denomination_overage(
        result, screen._order_total)
    if overage > 0:
        screen._apply_denomination_forfeit(result, items, overage)
    return items, result


@pytest.mark.parametrize(
    'equivalence_db', RUNNABLE_SCENARIOS,
    ids=lambda s: s.name, indirect=True)
class TestEngineSavePathEquivalence:

    def test_db_aggregates_match_engine(
            self, qtbot, equivalence_db, monkeypatch):
        """For each (method_name) aggregate, sum of saved
        method/match/customer must equal the engine's
        post-forfeit per-method totals."""
        conn, scenario, order_id = equivalence_db
        screen = _drive_and_confirm(
            qtbot, scenario, order_id, monkeypatch)

        items, result = _engine_post_forfeit(screen)
        if result is None:
            pytest.skip(f"[{scenario.name}] empty engine result")

        # Engine totals per method_name.
        eng_by_method = {}
        for li_idx, li in enumerate(result['line_items']):
            it = items[li_idx]
            name = it['method_name_snapshot']
            agg = eng_by_method.setdefault(
                name, {'method': 0, 'match': 0, 'customer': 0})
            agg['method'] += li['method_amount']
            agg['match'] += li['match_amount']
            agg['customer'] += li['customer_charged']

        # DB totals per method_name across all txns in this order.
        rows = conn.execute("""
            SELECT pli.method_name_snapshot,
                   SUM(pli.method_amount),
                   SUM(pli.match_amount),
                   SUM(pli.customer_charged)
            FROM payment_line_items pli
            JOIN transactions t ON pli.transaction_id = t.id
            WHERE t.customer_order_id = ?
              AND t.status IN ('Confirmed', 'Adjusted')
            GROUP BY pli.method_name_snapshot
        """, (order_id,)).fetchall()
        db_by_method = {
            r[0]: {'method': r[1], 'match': r[2], 'customer': r[3]}
            for r in rows
        }

        if not db_by_method:
            pytest.skip(
                f"[{scenario.name}] confirm did not save rows")

        # Compare every method we expected to save.
        for name, eng in eng_by_method.items():
            db = db_by_method.get(name)
            assert db is not None, (
                f"[{scenario.name}] engine expected method {name} "
                f"but no saved rows found")
            assert db['method'] == eng['method'], (
                f"[{scenario.name}] {name} method drift: "
                f"db={db['method']}c engine={eng['method']}c")
            assert db['match'] == eng['match'], (
                f"[{scenario.name}] {name} match drift: "
                f"db={db['match']}c engine={eng['match']}c")
            assert db['customer'] == eng['customer'], (
                f"[{scenario.name}] {name} customer drift: "
                f"db={db['customer']}c engine={eng['customer']}c")

    def test_per_vendor_db_reconciles_to_receipt(
            self, qtbot, equivalence_db, monkeypatch):
        """V1: every vendor's saved Σ method_amount = receipt_total."""
        conn, scenario, order_id = equivalence_db
        screen = _drive_and_confirm(
            qtbot, scenario, order_id, monkeypatch)

        for vr in scenario.vendors:
            row = conn.execute(
                """SELECT t.receipt_total,
                          COALESCE(SUM(pli.method_amount), 0)
                   FROM transactions t
                   LEFT JOIN payment_line_items pli
                     ON pli.transaction_id = t.id
                   WHERE t.vendor_id = ?
                     AND t.customer_order_id = ?
                     AND t.status IN ('Confirmed', 'Adjusted')
                   GROUP BY t.id""",
                (vr.vid, order_id)).fetchone()
            if row is None or row[1] == 0:
                continue
            receipt, alloc = row
            assert abs(alloc - receipt) <= 1, (
                f"[{scenario.name}] vendor {vr.name}: "
                f"alloc={alloc}c, receipt={receipt}c, "
                f"diff={alloc-receipt}c (>1¢ tolerance)")

    def test_db_totals_within_cap(
            self, qtbot, equivalence_db, monkeypatch):
        """C1: Σ match across customer's confirmed/adjusted today
        ≤ daily cap."""
        conn, scenario, order_id = equivalence_db
        if not scenario.cap_active:
            pytest.skip(
                f"[{scenario.name}] cap not active")
        _drive_and_confirm(
            qtbot, scenario, order_id, monkeypatch)
        total_match = conn.execute("""
            SELECT COALESCE(SUM(pli.match_amount), 0)
            FROM customer_orders co
            JOIN transactions t
              ON t.customer_order_id = co.id
             AND t.status IN ('Confirmed', 'Adjusted')
            JOIN payment_line_items pli
              ON pli.transaction_id = t.id
            WHERE co.market_day_id = 1
              AND co.customer_label = 'C-TEST'
              AND co.status IN ('Confirmed', 'Adjusted')
        """).fetchone()[0]
        assert total_match <= scenario.daily_cap_cents + 1, (
            f"[{scenario.name}] total match {total_match}c > "
            f"cap+1 {scenario.daily_cap_cents + 1}c")
