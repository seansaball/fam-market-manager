"""AdjustmentDialog ↔ PaymentScreen parity matrix.

Decision (2026-04-30): we are NOT unifying AdjustmentDialog and
PaymentScreen pre-launch (Option A from the audit-plan checkpoint).
Instead, every cap-aware financial behavior on PaymentScreen MUST
be mirrored — to the cent — by AdjustmentDialog.  This file pins
that contract.

Limitations
-----------
AdjustmentDialog edits ONE transaction at a time, so multi-vendor
scenarios from the cross-layer matrix can't run end-to-end through
AdjustmentDialog as a single dialog instance.  This file restricts
parity testing to single-vendor scenarios where the comparison is
direct.

Per-transaction parity is what matters — managers always interact
with one transaction at a time.
"""
from __future__ import annotations

import pytest

from tests.test_cross_layer_parity_matrix import SCENARIOS
from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


# Filter to scenarios where:
#   - Exactly one vendor (single-transaction order)
#   - All denom rows are bound to that vendor (or no binding)
SINGLE_VENDOR_SCENARIOS = [
    s for s in SCENARIOS
    if len(s.vendors) == 1
    and all(
        r.bound_vendor_id is None or r.bound_vendor_id == s.vendors[0].vid
        for r in s.rows
    )
]


@pytest.fixture
def parity_db(request, tmp_path, monkeypatch):
    """Build a fresh DB for one scenario and yield TWO order_ids:
    the first is for the PaymentScreen flow, the second is a
    confirmed transaction for the AdjustmentDialog flow.

    Yields ``(conn, scenario, payment_order_id, adjust_txn_id)``.
    """
    from fam.models.customer_order import (
        create_customer_order, update_customer_order_status,
    )
    from fam.models.transaction import (
        create_transaction, confirm_transaction,
        save_payment_line_items,
    )
    scenario = request.param

    db_file = str(tmp_path / f"parity_{scenario.name}.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()

    cap = scenario.daily_cap_cents
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', ?, ?)",
        (cap, 1 if scenario.cap_active else 0))
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

    # Each flow gets its OWN customer label and its own prior
    # consumption order so the two flows see equivalent prior-match
    # state without leaking match between flows.  The label split
    # also ensures one flow's saved data doesn't show up as
    # "prior" for the other.
    def _seed_prior(customer_label):
        if scenario.prior_match_cents <= 0:
            return
        prior_id, _ = create_customer_order(
            market_day_id=1, customer_label=customer_label,
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

    _seed_prior('C-PAY')
    _seed_prior('C-ADJ')

    # PaymentScreen-side order under C-PAY.
    payment_order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-PAY',
        zip_code='15102')
    for vr in scenario.vendors:
        create_transaction(
            market_day_id=1, vendor_id=vr.vid,
            receipt_total=vr.receipt_cents,
            customer_order_id=payment_order_id,
            market_day_date='2026-04-30')

    # AdjustmentDialog-side: separate confirmed txn under C-ADJ.
    adjust_order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-ADJ',
        zip_code='15102')
    vendor = scenario.vendors[0]
    adjust_txn_id, _ = create_transaction(
        market_day_id=1, vendor_id=vendor.vid,
        receipt_total=vendor.receipt_cents,
        customer_order_id=adjust_order_id,
        market_day_date='2026-04-30')
    save_payment_line_items(adjust_txn_id, [
        {'payment_method_id': 1,
         'method_name_snapshot': 'SNAP',
         'match_percent_snapshot': 100.0,
         'method_amount': vendor.receipt_cents,
         'match_amount': vendor.receipt_cents // 2,
         'customer_charged': vendor.receipt_cents - vendor.receipt_cents // 2,
         'photo_path': None, 'photo_source_paths': []}])
    confirm_transaction(adjust_txn_id, confirmed_by='T')
    update_customer_order_status(adjust_order_id, 'Confirmed')

    conn.commit()

    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, 'question',
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.No))

    yield conn, scenario, payment_order_id, adjust_txn_id
    close_connection()


def _drive_payment_screen(qtbot, scenario, payment_order_id):
    from fam.ui.payment_screen import PaymentScreen
    screen = PaymentScreen()
    qtbot.addWidget(screen)
    screen.load_customer_order(payment_order_id)
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
    return screen


def _drive_adjustment_dialog(qtbot, scenario, adjust_txn_id):
    from fam.ui.admin_screen import AdjustmentDialog
    from fam.models.transaction import get_transaction_by_id
    txn = get_transaction_by_id(adjust_txn_id)
    dialog = AdjustmentDialog(txn)
    qtbot.addWidget(dialog)
    while dialog._payment_rows:
        r = dialog._payment_rows[0]
        dialog.rows_layout.removeWidget(r)
        r.deleteLater()
        dialog._payment_rows.remove(r)
    for spec in scenario.rows:
        row = dialog._add_payment_row()
        combo = row.method_combo
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data and data.get('id') == spec.method_id:
                combo.setCurrentIndex(i)
                break
        if spec.charge_cents > 0:
            row._set_active_charge(spec.charge_cents)
    if scenario.use_auto_distribute:
        dialog._auto_distribute()
    else:
        dialog._on_payment_changed()
    return dialog


def _payment_engine_output(screen):
    """Mirror of PaymentScreen's _confirm_payment engine path:
    engine + forfeit + (Pass 4 give-back).  Returns line_items."""
    from fam.utils.calculations import calculate_payment_breakdown
    items = screen._collect_line_items()
    if not items:
        return None
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
    return result['line_items']


# ════════════════════════════════════════════════════════════════════
# Parity tests
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    'parity_db', SINGLE_VENDOR_SCENARIOS,
    ids=lambda s: s.name, indirect=True)
class TestAdjustmentDialogParity:
    """For every single-vendor scenario, AdjustmentDialog's output
    must equal PaymentScreen's engine output to the cent."""

    def test_per_line_method_match_customer_parity(
            self, qtbot, parity_db):
        """Adjustment ``get_new_line_items`` must produce the same
        method/match/customer per row as PaymentScreen's
        engine + forfeit chain."""
        conn, scenario, ps_order_id, ad_txn_id = parity_db

        ps = _drive_payment_screen(qtbot, scenario, ps_order_id)
        ps_lines = _payment_engine_output(ps)
        if ps_lines is None:
            pytest.skip(f"[{scenario.name}] no PaymentScreen output")

        dialog = _drive_adjustment_dialog(qtbot, scenario, ad_txn_id)
        ad_items = dialog.get_new_line_items()
        if not ad_items:
            pytest.skip(f"[{scenario.name}] no Adjustment output")

        for i, ad_it in enumerate(ad_items):
            if i >= len(ps_lines):
                break
            ps_li = ps_lines[i]
            assert ad_it['method_amount'] == ps_li['method_amount'], (
                f"[{scenario.name}] line[{i}] method drift: "
                f"adjust={ad_it['method_amount']}c, "
                f"payment={ps_li['method_amount']}c")
            assert ad_it['match_amount'] == ps_li['match_amount'], (
                f"[{scenario.name}] line[{i}] match drift: "
                f"adjust={ad_it['match_amount']}c, "
                f"payment={ps_li['match_amount']}c")
            assert (ad_it['customer_charged']
                    == ps_li['customer_charged']), (
                f"[{scenario.name}] line[{i}] customer drift: "
                f"adjust={ad_it['customer_charged']}c, "
                f"payment={ps_li['customer_charged']}c")

    def test_total_aggregates_parity(self, qtbot, parity_db):
        """Σ customer / Σ match / Σ method must agree."""
        conn, scenario, ps_order_id, ad_txn_id = parity_db

        ps = _drive_payment_screen(qtbot, scenario, ps_order_id)
        ps_lines = _payment_engine_output(ps)
        if ps_lines is None:
            pytest.skip(f"[{scenario.name}] no PaymentScreen output")

        dialog = _drive_adjustment_dialog(qtbot, scenario, ad_txn_id)
        ad_items = dialog.get_new_line_items()
        if not ad_items:
            pytest.skip(f"[{scenario.name}] no Adjustment output")

        ps_c = sum(li['customer_charged'] for li in ps_lines)
        ps_m = sum(li['match_amount'] for li in ps_lines)
        ps_a = sum(li['method_amount'] for li in ps_lines)
        ad_c = sum(it['customer_charged'] for it in ad_items)
        ad_m = sum(it['match_amount'] for it in ad_items)
        ad_a = sum(it['method_amount'] for it in ad_items)

        assert ad_c == ps_c, (
            f"[{scenario.name}] customer: adjust=${ad_c/100:.2f}, "
            f"payment=${ps_c/100:.2f}")
        assert ad_m == ps_m, (
            f"[{scenario.name}] match: adjust=${ad_m/100:.2f}, "
            f"payment=${ps_m/100:.2f}")
        assert ad_a == ps_a, (
            f"[{scenario.name}] method: adjust=${ad_a/100:.2f}, "
            f"payment=${ps_a/100:.2f}")

    def test_adjustment_per_line_invariant(
            self, qtbot, parity_db):
        """R1: customer + match = method on every adjustment row."""
        conn, scenario, _ps_id, ad_txn_id = parity_db
        dialog = _drive_adjustment_dialog(qtbot, scenario, ad_txn_id)
        ad_items = dialog.get_new_line_items()
        if not ad_items:
            pytest.skip(f"[{scenario.name}] no Adjustment output")
        for i, it in enumerate(ad_items):
            inv = it['customer_charged'] + it['match_amount']
            assert inv == it['method_amount'], (
                f"[{scenario.name}] adjust line[{i}] R1: "
                f"customer+match={inv}c != method={it['method_amount']}c")

    def test_adjustment_denom_unit_multiple(
            self, qtbot, parity_db):
        """R2: denom row customer is unit_count × denom on adjustment."""
        conn, scenario, _ps_id, ad_txn_id = parity_db
        dialog = _drive_adjustment_dialog(qtbot, scenario, ad_txn_id)
        ad_items = dialog.get_new_line_items()
        if not ad_items:
            pytest.skip(f"[{scenario.name}] no Adjustment output")
        for i, it in enumerate(ad_items):
            denom = it.get('denomination')
            if not (denom and denom > 0):
                continue
            assert it['customer_charged'] % denom == 0, (
                f"[{scenario.name}] adjust line[{i}] R2: "
                f"customer {it['customer_charged']}c not multiple "
                f"of denom {denom}c")
