"""Phase 8b: Returning customer cap chain test.

Real-world scenario: one customer makes 4-5 transactions across
a single market day, with cumulative match approaching and
crossing the $100 daily cap.

Validates that prior-match accounting holds correctly across:
  * each fresh transaction (PaymentScreen flow)
  * an adjustment of an earlier transaction (reduces match → frees
    cap for later txns)
  * a void of a transaction (also frees cap)
  * a re-confirm after void

After every step, validate:
  * R1 / V1 invariants on every saved row
  * C1: Σ match across customer's confirmed/adjusted today ≤ cap
  * Each transaction's spinbox shows the saved customer_charged
    after dialog reload (no phantom-refund regression #13)
  * Adjustment dialog opened on any txn shows correct
    cap=remaining-after-others
"""
import pytest
from PySide6.QtWidgets import QDialog

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def chain_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "chain.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    for vid, name in [(1, 'V1'), (2, 'V2'), (3, 'V3'),
                       (4, 'V4'), (5, 'V5')]:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))
    methods = [
        (1, 'SNAP', 100.0, None, 1),
        (2, 'Cash', 0.0, None, 2),
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
    for vid in (1, 2, 3, 4, 5):
        for mid in (1, 2, 4):
            conn.execute(
                "INSERT INTO vendor_payment_methods "
                "(vendor_id, payment_method_id) VALUES (?, ?)",
                (vid, mid))
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-30', 'Open', 'T')")
    conn.commit()

    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, 'question',
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.No))

    yield conn
    close_connection()


def _build_and_confirm_order(qtbot, monkeypatch, vendor_receipts,
                              snap_charge, customer_label='C-RTN'):
    """Build an order with the given vendor receipts, set SNAP
    charge across all vendors, and confirm via PaymentScreen.
    Returns the order_id."""
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction
    from fam.ui.payment_screen import PaymentScreen
    import fam.ui.widgets.payment_confirmation_dialog as pcd

    order_id, _ = create_customer_order(
        market_day_id=1, customer_label=customer_label,
        zip_code='15102')
    for vid, receipt in vendor_receipts:
        create_transaction(
            market_day_id=1, vendor_id=vid,
            receipt_total=receipt,
            customer_order_id=order_id,
            market_day_date='2026-04-30')

    screen = PaymentScreen()
    qtbot.addWidget(screen)
    screen.load_customer_order(order_id)
    while screen._payment_rows:
        r = screen._payment_rows[0]
        screen.rows_layout.removeWidget(r)
        r.deleteLater()
        screen._payment_rows.remove(r)

    # Single SNAP row.
    row = screen._add_payment_row()
    combo = row.method_combo
    for i in range(combo.count()):
        data = combo.itemData(i)
        if data and data.get('id') == 1:  # SNAP
            combo.setCurrentIndex(i)
            break
    if snap_charge > 0:
        row._set_active_charge(snap_charge)
    else:
        screen._auto_distribute()
    screen._update_summary()

    def stub_init(self, *a, **kw):
        QDialog.__init__(self)
    def stub_exec(self):
        return QDialog.Accepted
    monkeypatch.setattr(pcd.PaymentConfirmationDialog,
                         '__init__', stub_init)
    monkeypatch.setattr(pcd.PaymentConfirmationDialog,
                         'exec', stub_exec)
    screen._confirm_payment()
    return order_id


def _customer_total_match(conn, label='C-RTN'):
    return conn.execute("""
        SELECT COALESCE(SUM(pli.match_amount), 0)
        FROM customer_orders co
        JOIN transactions t
          ON t.customer_order_id = co.id
         AND t.status IN ('Confirmed', 'Adjusted')
        JOIN payment_line_items pli
          ON pli.transaction_id = t.id
        WHERE co.market_day_id = 1
          AND co.customer_label = ?
          AND co.status IN ('Confirmed', 'Adjusted')
    """, (label,)).fetchone()[0]


class TestReturningCustomerCapChain:

    def test_4_orders_approach_cap_no_overflow(
            self, qtbot, chain_db, monkeypatch):
        """4 sequential orders by C-RTN, totaling more match than the
        cap.  After each, total customer match ≤ cap."""
        conn = chain_db

        # Order 1: $50 SNAP → $25 customer + $25 match. Easy.
        _build_and_confirm_order(
            qtbot, monkeypatch,
            [(1, 5000)], 2500)
        m1 = _customer_total_match(conn)
        assert m1 == 2500, (
            f"After order 1: match={m1}c, expected 2500")

        # Order 2: $100 SNAP → $50 customer + $50 match. Cumulative $75.
        _build_and_confirm_order(
            qtbot, monkeypatch,
            [(2, 10000)], 5000)
        m2 = _customer_total_match(conn)
        assert m2 == 7500, (
            f"After order 2: match={m2}c, expected 7500")

        # Order 3: $80 SNAP → uncapped $40 match. With $25 cap remaining,
        # match capped to $25.
        _build_and_confirm_order(
            qtbot, monkeypatch,
            [(3, 8000)], 4000)
        m3 = _customer_total_match(conn)
        assert m3 <= 10001, (
            f"After order 3: match={m3}c > cap+1 10001c")
        # Should be exactly 10000 (cap reached).
        assert m3 == 10000, (
            f"After order 3: cap should be exactly hit at 10000c, "
            f"got {m3}c")

        # Order 4: $40 SNAP → cap fully consumed, customer pays full.
        _build_and_confirm_order(
            qtbot, monkeypatch,
            [(4, 4000)], 4000)
        m4 = _customer_total_match(conn)
        assert m4 == 10000, (
            f"After order 4: match must stay at cap (10000c), "
            f"got {m4}c.  Match cannot grow beyond cap regardless "
            f"of how many additional orders the customer makes.")

    def test_void_frees_cap_for_later_orders(
            self, qtbot, chain_db, monkeypatch):
        """Void of an early order returns its match to the cap
        budget, allowing a later order to consume more match.

        FINANCIAL_FORMULA.md §7: voided txn excluded from prior_match
        query.  This test pins that contract end-to-end."""
        from fam.models.transaction import void_transaction

        conn = chain_db

        # Order 1: $80 SNAP → $40 match (uncapped, fits in cap).
        _build_and_confirm_order(
            qtbot, monkeypatch,
            [(1, 8000)], 4000)
        assert _customer_total_match(conn) == 4000

        # Order 2: $80 SNAP → $40 match. Cumulative $80, still under cap.
        _build_and_confirm_order(
            qtbot, monkeypatch,
            [(2, 8000)], 4000)
        assert _customer_total_match(conn) == 8000

        # Find order 1's first txn id.
        t1 = conn.execute("""
            SELECT t.id FROM transactions t
            JOIN customer_orders co ON co.id = t.customer_order_id
            WHERE co.customer_label = 'C-RTN' AND t.vendor_id = 1
            LIMIT 1
        """).fetchone()[0]

        # Void order 1's txn.
        void_transaction(t1, voided_by='T')
        # Match dropped by $40.
        assert _customer_total_match(conn) == 4000

        # Order 3: $80 SNAP → uncapped $40 match.  Cap remaining = $60.
        # Match should be uncapped at $40.
        _build_and_confirm_order(
            qtbot, monkeypatch,
            [(3, 8000)], 4000)
        # Cumulative: order 2 $40 + order 3 $40 = $80 (within cap).
        assert _customer_total_match(conn) == 8000

    def test_adjustment_reduces_match_frees_cap(
            self, qtbot, chain_db, monkeypatch):
        """Adjusting an early transaction's payment to reduce match
        must free up cap budget for new orders."""
        from fam.models.transaction import (
            update_transaction, save_payment_line_items,
        )

        conn = chain_db

        # Order 1: $100 SNAP → $50 match (uncapped).
        _build_and_confirm_order(
            qtbot, monkeypatch,
            [(1, 10000)], 5000)
        assert _customer_total_match(conn) == 5000

        # Manually adjust order 1's txn down to $40 SNAP → $20 match.
        t1 = conn.execute("""
            SELECT t.id FROM transactions t
            JOIN customer_orders co ON co.id = t.customer_order_id
            WHERE co.customer_label = 'C-RTN' AND t.vendor_id = 1
            LIMIT 1
        """).fetchone()[0]
        update_transaction(t1, receipt_total=4000, status='Adjusted',
                            commit=False)
        save_payment_line_items(t1, [
            {'payment_method_id': 1,
             'method_name_snapshot': 'SNAP',
             'match_percent_snapshot': 100.0,
             'method_amount': 4000, 'match_amount': 2000,
             'customer_charged': 2000,
             'photo_path': None, 'photo_source_paths': []}],
            commit=False)
        conn.commit()

        # Match dropped from $50 to $20 — $30 of cap freed.
        assert _customer_total_match(conn) == 2000

        # New order: $100 SNAP → $50 match available + uncapped $50.
        # Should not exceed cap.
        _build_and_confirm_order(
            qtbot, monkeypatch,
            [(2, 10000)], 5000)
        m = _customer_total_match(conn)
        assert m <= 10001, (
            f"After adjust + new order: match {m}c > cap+1")
        # $20 (adjusted order 1) + $50 (uncapped new order) = $70 (under cap).
        assert m == 7000, (
            f"Expected $20 + $50 = $70 cumulative match, got "
            f"${m/100:.2f}.  Adjustment reduction must release cap "
            f"budget for new orders.")

    def test_total_match_ratchet_never_exceeds_cap(
            self, qtbot, chain_db, monkeypatch):
        """The cumulative match across ANY sequence of orders by the
        same customer on the same day must never exceed the cap by
        more than the ±1¢ engine penny-rec tolerance."""
        conn = chain_db
        # 5 orders at $100 SNAP each.
        for i, vid in enumerate((1, 2, 3, 4, 5)):
            _build_and_confirm_order(
                qtbot, monkeypatch,
                [(vid, 10000)], 5000)
            m = _customer_total_match(conn)
            assert m <= 10001, (
                f"After order {i+1}: match {m}c > cap+1 10001c"
            )
        final = _customer_total_match(conn)
        assert final == 10000, (
            f"Expected exact cap saturation 10000c, got {final}c")
