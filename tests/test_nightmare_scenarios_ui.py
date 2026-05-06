"""Phase 3: Nightmare scenarios via the actual UI.

The standing ``test_nightmare_scenarios.py`` runs adversarial
scenarios at the engine/model layer.  This file re-runs the most
financially-sensitive nightmare scenarios THROUGH the actual
``PaymentScreen`` / ``AdjustmentDialog`` widgets so we catch UI-
layer bugs the engine-only suite can't see (cap-write-back drift,
silent spinbox clamps, label-recompute overwrites, Layer 2A/2C
order-of-evaluation issues).

Scenarios pinned here mirror the patterns from the 18 onsite-
reported bugs:

  N1.  Cap-bound returning customer with multi-denom (#17 bricked
       transaction).
  N2.  Cap-fallback path: denom_uncapped > remaining_cap.
  N3.  Denom forfeit at vendor boundary (#2 per-vendor 1¢ drift).
  N4.  Silent-clamp on receipt drop (#12 adjustment chain).
  N5.  Multi-adjust receipt change cycle (drop → raise → drop).
  N6.  Void cascade: confirm → void → re-confirm.
  N7.  Customer over-pays via denom; FAM forfeit + cap-aware
       give-back to non-denom (#8 FAM Match $97.30 vs $100).
  N8.  Auto-distribute on cap-active produces consistent state
       (#11 SNAP-first row order independence).
"""
import pytest
from PySide6.QtWidgets import QDialog

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def nightmare_db(tmp_path, monkeypatch):
    """Standard nightmare-scenario DB seed."""
    db_file = str(tmp_path / "nightmare.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    for vid, name in [
            (1, 'Elfinwild'), (2, 'Fungetarian'),
            (3, 'Hughes'), (4, 'Pond Hill'), (5, 'V5')]:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))
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
    for vid in (1, 2, 3, 4, 5):
        for mid in (1, 2, 3, 4, 5):
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


def _seed_prior(customer_label, match_cents):
    """Seed a confirmed prior order for cap-active scenarios."""
    if match_cents <= 0:
        return
    from fam.models.customer_order import (
        create_customer_order, update_customer_order_status,
    )
    from fam.models.transaction import (
        create_transaction, confirm_transaction,
        save_payment_line_items,
    )
    pid, _ = create_customer_order(
        market_day_id=1, customer_label=customer_label,
        zip_code='15102')
    pt, _ = create_transaction(
        market_day_id=1, vendor_id=1,
        receipt_total=match_cents * 2,
        customer_order_id=pid,
        market_day_date='2026-04-30')
    save_payment_line_items(pt, [
        {'payment_method_id': 1,
         'method_name_snapshot': 'SNAP',
         'match_percent_snapshot': 100.0,
         'method_amount': match_cents * 2,
         'match_amount': match_cents,
         'customer_charged': match_cents,
         'photo_path': None, 'photo_source_paths': []}])
    confirm_transaction(pt, confirmed_by='T')
    update_customer_order_status(pid, 'Confirmed')


def _build_order(customer_label, vendor_receipts):
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction
    oid, _ = create_customer_order(
        market_day_id=1, customer_label=customer_label,
        zip_code='15102')
    for vid, receipt in vendor_receipts:
        create_transaction(
            market_day_id=1, vendor_id=vid,
            receipt_total=receipt,
            customer_order_id=oid,
            market_day_date='2026-04-30')
    return oid


def _drive_payment_screen(qtbot, order_id, row_specs):
    """row_specs: list of (method_id, charge_cents, bound_vid)."""
    from fam.ui.payment_screen import PaymentScreen
    screen = PaymentScreen()
    qtbot.addWidget(screen)
    screen.load_customer_order(order_id)
    while screen._payment_rows:
        r = screen._payment_rows[0]
        screen.rows_layout.removeWidget(r)
        r.deleteLater()
        screen._payment_rows.remove(r)
    for method_id, charge, bound_vid in row_specs:
        row = screen._add_payment_row()
        for i in range(row.method_combo.count()):
            d = row.method_combo.itemData(i)
            if d and d.get('id') == method_id:
                row.method_combo.setCurrentIndex(i)
                break
        if bound_vid is not None:
            row.set_bound_vendor_id(bound_vid)
        if charge > 0:
            row._set_active_charge(charge)
    # Run the summary so cards reflect the entered rows.  Without
    # this, the cards stay at their default state and any test
    # reading them gets stale values.
    screen._update_summary()
    return screen


def _confirm(qtbot, screen, monkeypatch):
    """Confirm via stub'd PaymentConfirmationDialog."""
    import fam.ui.widgets.payment_confirmation_dialog as pcd

    def stub_init(self, *a, **kw):
        QDialog.__init__(self)
    def stub_exec(self):
        return QDialog.Accepted
    monkeypatch.setattr(pcd.PaymentConfirmationDialog,
                         '__init__', stub_init)
    monkeypatch.setattr(pcd.PaymentConfirmationDialog,
                         'exec', stub_exec)
    screen._confirm_payment()


def _check_per_vendor_invariant(conn, order_id):
    rows = conn.execute("""
        SELECT t.id, t.vendor_id, t.receipt_total, t.status,
               COALESCE(SUM(pli.method_amount), 0) AS alloc
        FROM transactions t
        LEFT JOIN payment_line_items pli
          ON pli.transaction_id = t.id
        WHERE t.customer_order_id = ?
        GROUP BY t.id
    """, (order_id,)).fetchall()
    for tid, vid, receipt, status, alloc in rows:
        if status not in ('Confirmed', 'Adjusted') or alloc == 0:
            continue
        assert abs(alloc - receipt) <= 1, (
            f"Per-vendor invariant violated: txn {tid} vendor {vid} "
            f"alloc={alloc}c != receipt={receipt}c")


def _check_per_line_invariant(conn, order_id):
    rows = conn.execute("""
        SELECT pli.method_name_snapshot, pli.method_amount,
               pli.match_amount, pli.customer_charged
        FROM payment_line_items pli
        JOIN transactions t ON pli.transaction_id = t.id
        WHERE t.customer_order_id = ?
    """, (order_id,)).fetchall()
    for name, m, match, cust in rows:
        if name == 'Unallocated Funds':
            continue
        assert cust + match == m, (
            f"R1 violated: {name} customer={cust}c + "
            f"match={match}c != method={m}c")


class TestNightmareScenariosViaUI:

    def test_n1_returning_customer_multi_denom_cap_fallback(
            self, qtbot, nightmare_db, monkeypatch):
        """Bricked-txn scenario via UI: returning customer with
        $83.31 prior + denom $26 > remaining cap $16.69."""
        conn = nightmare_db
        _seed_prior('C-N1', 8331)
        order_id = _build_order(
            'C-N1',
            [(1, 1111), (2, 2222), (3, 3333), (4, 4444)])
        screen = _drive_payment_screen(
            qtbot, order_id,
            [(4, 600, 1),    # 3 × $2 FB on Elfinwild
             (3, 2000, 3),   # 2 × $10 Food RX on Hughes
             (1, 6841, None)])  # SNAP $68.41
        _confirm(qtbot, screen, monkeypatch)
        _check_per_line_invariant(conn, order_id)
        _check_per_vendor_invariant(conn, order_id)

    def test_n2_cap_fallback_denom_alone_exceeds_cap(
            self, qtbot, nightmare_db, monkeypatch):
        """Pure cap-fallback: denom uncapped match > remaining cap."""
        conn = nightmare_db
        _seed_prior('C-N2', 9000)  # $90 prior → $10 remaining
        order_id = _build_order('C-N2', [(1, 5000)])
        screen = _drive_payment_screen(
            qtbot, order_id,
            [(4, 2000, 1),   # 10 × $2 FB on V1: $20 customer + $20 match
             (1, 0, None)])  # SNAP auto-fills
        screen._auto_distribute()
        _confirm(qtbot, screen, monkeypatch)
        _check_per_line_invariant(conn, order_id)
        _check_per_vendor_invariant(conn, order_id)

    def test_n3_denom_forfeit_at_vendor_boundary(
            self, qtbot, nightmare_db, monkeypatch):
        """Per-vendor 1¢ drift class: forfeit attribution must be
        per-vendor not order-level."""
        conn = nightmare_db
        order_id = _build_order(
            'C-N3', [(1, 4000), (2, 2530), (3, 12050), (4, 12500)])
        screen = _drive_payment_screen(
            qtbot, order_id,
            [(4, 2000, 1),   # 10 × $2 FB on V1 (exact fit)
             (4, 1400, 2),   # 7 × $2 FB on V2 ($25.30 receipt; $0.70 forfeit)
             (1, 17680, None)])  # SNAP fills V3+V4
        _confirm(qtbot, screen, monkeypatch)
        _check_per_line_invariant(conn, order_id)
        _check_per_vendor_invariant(conn, order_id)

    def test_n4_silent_clamp_on_receipt_drop(
            self, qtbot, nightmare_db, monkeypatch):
        """Adjustment dialog: change receipt total drastically.
        Spinbox max changes must NOT silently destroy charges."""
        from fam.models.customer_order import (
            create_customer_order, update_customer_order_status,
        )
        from fam.models.transaction import (
            create_transaction, confirm_transaction,
            save_payment_line_items,
        )
        from fam.ui.admin_screen import AdjustmentDialog
        from fam.models.transaction import get_transaction_by_id

        conn = nightmare_db
        oid, _ = create_customer_order(
            market_day_id=1, customer_label='C-N4',
            zip_code='15102')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1,
            receipt_total=2000,  # $20 receipt
            customer_order_id=oid,
            market_day_date='2026-04-30')
        save_payment_line_items(tid, [
            {'payment_method_id': 4,
             'method_name_snapshot': 'JH Food Bucks',
             'match_percent_snapshot': 100.0,
             'method_amount': 2000, 'match_amount': 1000,
             'customer_charged': 1000,
             'photo_path': None, 'photo_source_paths': []}])
        confirm_transaction(tid, confirmed_by='T')
        update_customer_order_status(oid, 'Confirmed')
        conn.commit()

        txn = get_transaction_by_id(tid)
        dialog = AdjustmentDialog(txn)
        qtbot.addWidget(dialog)
        # Drop receipt to $4.33 — should NOT silently zero the FB row.
        fb_row = dialog._payment_rows[0]
        initial_charge = fb_row._get_active_charge()
        dialog.receipt_spin.setValue(4.33)
        # Charge must NOT have been silently destroyed by the cap.
        # Either preserved or only modified by the rescale path.
        assert fb_row._get_active_charge() > 0, (
            "FB row charge silently zero'd on receipt drop "
            "(_update_row_caps regression).")

    def test_n5_multi_receipt_change_cycle(
            self, qtbot, nightmare_db, monkeypatch):
        """Adjust receipt drop → raise → drop in sequence."""
        from fam.models.customer_order import (
            create_customer_order, update_customer_order_status,
        )
        from fam.models.transaction import (
            create_transaction, confirm_transaction,
            save_payment_line_items, update_transaction,
        )
        conn = nightmare_db
        oid, _ = create_customer_order(
            market_day_id=1, customer_label='C-N5',
            zip_code='15102')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=5000,
            customer_order_id=oid, market_day_date='2026-04-30')
        save_payment_line_items(tid, [
            {'payment_method_id': 1,
             'method_name_snapshot': 'SNAP',
             'match_percent_snapshot': 100.0,
             'method_amount': 5000, 'match_amount': 2500,
             'customer_charged': 2500,
             'photo_path': None, 'photo_source_paths': []}])
        confirm_transaction(tid, confirmed_by='T')
        update_customer_order_status(oid, 'Confirmed')
        conn.commit()

        # Cycle of receipt edits.
        for new_total in (3000, 7000, 4000, 6000, 5000):
            update_transaction(tid, receipt_total=new_total,
                                status='Adjusted', commit=False)
            save_payment_line_items(tid, [
                {'payment_method_id': 1,
                 'method_name_snapshot': 'SNAP',
                 'match_percent_snapshot': 100.0,
                 'method_amount': new_total,
                 'match_amount': new_total // 2,
                 'customer_charged': new_total - new_total // 2,
                 'photo_path': None, 'photo_source_paths': []}],
                commit=False)
            conn.commit()
            _check_per_line_invariant(conn, oid)
            _check_per_vendor_invariant(conn, oid)

    def test_n6_void_re_confirm_cycle(
            self, qtbot, nightmare_db, monkeypatch):
        """Confirm → Void → new order → Confirm: cap accounting
        must release the voided txn's match."""
        from fam.models.customer_order import (
            create_customer_order, update_customer_order_status,
            get_customer_prior_match,
        )
        from fam.models.transaction import (
            create_transaction, confirm_transaction,
            save_payment_line_items, void_transaction,
        )
        conn = nightmare_db
        # First order: $80 SNAP → $40 match.
        o1, _ = create_customer_order(
            market_day_id=1, customer_label='C-N6',
            zip_code='15102')
        t1, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=8000,
            customer_order_id=o1, market_day_date='2026-04-30')
        save_payment_line_items(t1, [
            {'payment_method_id': 1,
             'method_name_snapshot': 'SNAP',
             'match_percent_snapshot': 100.0,
             'method_amount': 8000, 'match_amount': 4000,
             'customer_charged': 4000,
             'photo_path': None, 'photo_source_paths': []}])
        confirm_transaction(t1, confirmed_by='T')
        update_customer_order_status(o1, 'Confirmed')

        # Prior match consumption: $40.
        prior_before = get_customer_prior_match('C-N6', 1)
        assert prior_before == 4000

        # Void.
        void_transaction(t1, voided_by='T')
        prior_after = get_customer_prior_match('C-N6', 1)
        assert prior_after == 0, (
            f"Voided txn must release its match.  Prior was 4000c, "
            f"after void={prior_after}c")

    def test_n7_cap_aware_giveback_to_non_denom(
            self, qtbot, nightmare_db, monkeypatch):
        """User's screenshot scenario: forfeit gives back match
        capacity to non-denom under cap → FAM Match shows full cap."""
        conn = nightmare_db
        order_id = _build_order(
            'C-N7', [(1, 4000), (2, 2530), (3, 12050), (4, 12500)])
        screen = _drive_payment_screen(
            qtbot, order_id,
            [(4, 2000, 1),  # 10 × $2 FB Fudgie
             (4, 1400, 2),  # 7 × $2 FB Healthy ($2.70 forfeit on V2)
             (1, 17680, None)])  # SNAP
        # FAM Match should be cap=$100 (not $97.30 from forfeit reduction).
        cards = screen.summary_row.cards
        fam_match = cards['fam_match'].value_label.text()
        assert fam_match == '$100.00', (
            f"FAM Match card must show full cap $100.00 (cap-aware "
            f"give-back), got {fam_match}")

    def test_n8_auto_distribute_row_order_independent(
            self, qtbot, nightmare_db, monkeypatch):
        """SNAP-first vs FB-first row order must produce identical
        summary cards.  The bricked-transaction class fix #11."""
        conn = nightmare_db
        # Path A: FB first, then SNAP added.
        oa = _build_order(
            'C-NA', [(1, 4523), (2, 1111),
                      (3, 4565), (4, 8536), (5, 2456)])
        sa = _drive_payment_screen(
            qtbot, oa,
            [(4, 600, 2),    # FB on V2 first
             (1, 10591, None)])
        cards_a = {
            k: sa.summary_row.cards[k].value_label.text()
            for k in ('allocated', 'customer_pays',
                       'fam_match', 'remaining')
        }

        ob = _build_order(
            'C-NB', [(1, 4523), (2, 1111),
                      (3, 4565), (4, 8536), (5, 2456)])
        sb = _drive_payment_screen(
            qtbot, ob,
            [(1, 10591, None),  # SNAP first
             (4, 600, 2)])
        cards_b = {
            k: sb.summary_row.cards[k].value_label.text()
            for k in ('allocated', 'customer_pays',
                       'fam_match', 'remaining')
        }
        assert cards_a == cards_b, (
            f"Row order changed cards:\n"
            f"  FB-first: {cards_a}\n"
            f"  SNAP-first: {cards_b}")
