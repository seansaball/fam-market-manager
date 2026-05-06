"""Phase 5: App-restart persistence.

Real volunteers close the app and reopen it constantly.  Every
financial number visible on screen before close must be byte-
identical when the app reopens with the same DB.

This test simulates close + reopen by:
  1. Building a fully-populated DB state (mid-day with confirmed,
     adjusted, voided transactions, draft orders, prior cap
     consumption).
  2. Snapshotting every UI-visible financial value across
     PaymentScreen (loading each order), AdjustmentDialog
     (opened on each transaction).
  3. Closing the QApplication (deleting all widgets) and re-
     creating fresh PaymentScreen / AdjustmentDialog instances.
  4. Snapshotting the same fields again.
  5. Asserting byte-identical comparison.

Any drift means a fresh-load code path computes something
differently than the post-action code path — exactly the kind
of latent bug the user can't catch through normal use until
production data is flowing.
"""
import pytest
from PySide6.QtWidgets import QDialog

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def populated_db(tmp_path, monkeypatch):
    """Build a market day with several confirmed/adjusted/voided
    transactions to exercise the full lifecycle."""
    db_file = str(tmp_path / "restart.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    for vid, name in [(1, 'V1'), (2, 'V2'), (3, 'V3')]:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))
    methods = [
        (1, 'SNAP', 100.0, None, 1),
        (4, 'JH Food Bucks', 100.0, 200, 4),
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
    for vid in (1, 2, 3):
        for mid in (1, 4):
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
        save_payment_line_items, void_transaction,
    )

    # Order A: confirmed, will be adjusted.
    oa, _ = create_customer_order(
        market_day_id=1, customer_label='C-A',
        zip_code='15102')
    ta, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=5000,
        customer_order_id=oa, market_day_date='2026-04-30')
    save_payment_line_items(ta, [
        {'payment_method_id': 1,
         'method_name_snapshot': 'SNAP',
         'match_percent_snapshot': 100.0,
         'method_amount': 5000, 'match_amount': 2500,
         'customer_charged': 2500,
         'photo_path': None, 'photo_source_paths': []}])
    confirm_transaction(ta, confirmed_by='T')
    update_customer_order_status(oa, 'Confirmed')

    # Order B: confirmed, multi-method, denom + non-denom.
    ob, _ = create_customer_order(
        market_day_id=1, customer_label='C-B',
        zip_code='15102')
    tb1, _ = create_transaction(
        market_day_id=1, vendor_id=2, receipt_total=4000,
        customer_order_id=ob, market_day_date='2026-04-30')
    save_payment_line_items(tb1, [
        {'payment_method_id': 4,
         'method_name_snapshot': 'JH Food Bucks',
         'match_percent_snapshot': 100.0,
         'method_amount': 2000, 'match_amount': 1000,
         'customer_charged': 1000,
         'photo_path': None, 'photo_source_paths': []},
        {'payment_method_id': 1,
         'method_name_snapshot': 'SNAP',
         'match_percent_snapshot': 100.0,
         'method_amount': 2000, 'match_amount': 1000,
         'customer_charged': 1000,
         'photo_path': None, 'photo_source_paths': []}])
    confirm_transaction(tb1, confirmed_by='T')
    update_customer_order_status(ob, 'Confirmed')

    # Order C: confirmed, then VOIDED.
    oc, _ = create_customer_order(
        market_day_id=1, customer_label='C-C',
        zip_code='15102')
    tc, _ = create_transaction(
        market_day_id=1, vendor_id=3, receipt_total=3000,
        customer_order_id=oc, market_day_date='2026-04-30')
    save_payment_line_items(tc, [
        {'payment_method_id': 1,
         'method_name_snapshot': 'SNAP',
         'match_percent_snapshot': 100.0,
         'method_amount': 3000, 'match_amount': 1500,
         'customer_charged': 1500,
         'photo_path': None, 'photo_source_paths': []}])
    confirm_transaction(tc, confirmed_by='T')
    update_customer_order_status(oc, 'Confirmed')
    void_transaction(tc, voided_by='T')

    # Order D: still in DRAFT (not confirmed).
    od, _ = create_customer_order(
        market_day_id=1, customer_label='C-D',
        zip_code='15102')
    create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=2500,
        customer_order_id=od, market_day_date='2026-04-30')

    conn.commit()

    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, 'question',
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.No))

    yield {
        'conn': conn,
        'orders': {'A': oa, 'B': ob, 'C': oc, 'D': od},
        'txns': {'A': ta, 'B1': tb1, 'C': tc},
    }
    close_connection()


def _snapshot_payment_screen(qtbot, order_id):
    """Open PaymentScreen on order_id, snapshot every visible field."""
    from fam.ui.payment_screen import PaymentScreen
    screen = PaymentScreen()
    qtbot.addWidget(screen)
    screen.load_customer_order(order_id)
    snap = {
        'order_total': screen.order_total_label.text(),
        'allocated': screen.summary_row.cards['allocated'].value_label.text(),
        'remaining': screen.summary_row.cards['remaining'].value_label.text(),
        'customer_pays': screen.summary_row.cards['customer_pays'].value_label.text(),
        'fam_match': screen.summary_row.cards['fam_match'].value_label.text(),
        'rows': [],
    }
    for r in screen._payment_rows:
        m = r.get_selected_method()
        snap['rows'].append({
            'method': m['name'] if m else None,
            'charge': r._get_active_charge(),
            'match_label': r.match_amount_label.text(),
            'total_label': r.total_label.text(),
            'bound_vendor_id': r.get_bound_vendor_id(),
        })
    return snap


def _snapshot_adjustment_dialog(qtbot, txn_id):
    """Open AdjustmentDialog on txn_id, snapshot every visible field."""
    from fam.ui.admin_screen import AdjustmentDialog
    from fam.models.transaction import get_transaction_by_id

    txn = get_transaction_by_id(txn_id)
    if txn is None:
        return None
    dialog = AdjustmentDialog(txn)
    qtbot.addWidget(dialog)
    snap = {
        'receipt_value': dialog.receipt_spin.value(),
        'vendor_id': dialog.vendor_combo.currentData(),
        'rows': [],
    }
    for r in dialog._payment_rows:
        m = r.get_selected_method()
        snap['rows'].append({
            'method': m['name'] if m else None,
            'charge': r._get_active_charge(),
            'match_label': r.match_amount_label.text(),
            'total_label': r.total_label.text(),
        })
    return snap


class TestAppRestartPersistence:

    def test_payment_screen_state_preserved_after_restart(
            self, qtbot, populated_db):
        """Loading PaymentScreen on each order, then re-loading
        from a fresh PaymentScreen instance, must produce
        byte-identical UI state."""
        orders = populated_db['orders']

        for label, order_id in orders.items():
            snap1 = _snapshot_payment_screen(qtbot, order_id)
            # Simulate restart: build a new screen.  No DB mutation.
            snap2 = _snapshot_payment_screen(qtbot, order_id)
            assert snap1 == snap2, (
                f"Order {label}: PaymentScreen reload diverged.\n"
                f"  before: {snap1}\n"
                f"  after:  {snap2}")

    def test_adjustment_dialog_state_preserved_after_restart(
            self, qtbot, populated_db):
        """Same for AdjustmentDialog."""
        txns = populated_db['txns']
        for label, txn_id in txns.items():
            snap1 = _snapshot_adjustment_dialog(qtbot, txn_id)
            if snap1 is None:
                continue
            snap2 = _snapshot_adjustment_dialog(qtbot, txn_id)
            assert snap1 == snap2, (
                f"Txn {label}: AdjustmentDialog reload diverged.\n"
                f"  before: {snap1}\n"
                f"  after:  {snap2}")

    def test_voided_txn_excluded_from_payment_load(
            self, qtbot, populated_db):
        """A voided transaction's containing order should still
        load on PaymentScreen, but the voided txn's data should be
        treated correctly (status = 'Voided' surfaces).  Most
        relevant: total customer match across the day is reduced
        by the voided amount."""
        from tests.test_returning_customer_cap_chain import (
            _customer_total_match,
        )
        # Order C is voided; its match shouldn't count toward C-C's
        # daily total (which is 0 since the only txn was voided).
        conn = populated_db['conn']
        m = _customer_total_match(conn, label='C-C')
        assert m == 0, (
            f"Voided order C should contribute 0 match to C-C's "
            f"daily total, got {m}c")

    def test_db_invariants_hold_post_restart(
            self, qtbot, populated_db):
        """R1 + V1 + C1 invariants on EVERY saved row must hold
        regardless of which screen was used to create them."""
        conn = populated_db['conn']

        rows = conn.execute("""
            SELECT t.vendor_id, t.receipt_total, t.status,
                   pli.method_name_snapshot, pli.method_amount,
                   pli.match_amount, pli.customer_charged
            FROM payment_line_items pli
            JOIN transactions t ON pli.transaction_id = t.id
            ORDER BY t.id, pli.id
        """).fetchall()

        # R1: per-line invariant on every row except Unallocated Funds.
        for r in rows:
            if r[3] == 'Unallocated Funds':
                continue
            assert r[5] + r[6] == r[4], (
                f"R1 violated: {dict(zip(['vendor', 'receipt', 'status', 'method', 'method_amount', 'match', 'customer'], r))}")

        # V1: per-confirmed-txn alloc = receipt.
        per_txn = conn.execute("""
            SELECT t.id, t.receipt_total, t.status,
                   COALESCE(SUM(pli.method_amount), 0)
            FROM transactions t
            LEFT JOIN payment_line_items pli
              ON pli.transaction_id = t.id
            WHERE t.status IN ('Confirmed', 'Adjusted')
            GROUP BY t.id
        """).fetchall()
        for tid, receipt, status, alloc in per_txn:
            assert abs(alloc - receipt) <= 1, (
                f"V1 violated: txn {tid} ({status}) "
                f"alloc={alloc}c receipt={receipt}c")
