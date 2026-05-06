"""Regression: cap-aware forfeit give-back + post-cap row labels.

User-reported screenshot (2026-04-30 onsite, customer C-001-LB1):

  Order $310.80 across 4 vendors (Fudgie $40, Healthy $25.30,
  Jill's $120.50, Kizzle $125.00).  Daily match cap $100.

  Volunteer enters:
    10 × JH Food Bucks  → Fudgie     ($20 charge, denom)
     7 × JH Food Bucks  → Healthy    ($14 charge, denom)
       Auto-Distribute fills SNAP    (overflow row)

Two bugs in the resulting screen state:

  Bug #1 (UI level)
    SNAP row labels showed Match=$179.50, Total=$359.00 — the
    UNCAPPED charge × match% — instead of the engine's post-cap
    Match=$66.00, Total=$245.50.  ``_update_summary`` was
    calling ``row._recompute()`` after ``_set_active_charge``,
    which overwrote ``set_display_values()``'s engine values
    with the spinbox-derived formula.

  Bug #2 (logic level)
    FAM Match card showed $97.30 instead of $100, even though
    the cap was active and SNAP had headroom.  The denomination
    forfeit reduced denom match by $2.70 but didn't give that
    capacity back to non-denom rows under the cap, so FAM
    silently dropped below the cap for no reason.  The user
    expected: when the cap is met, FAM Match shows the full cap
    and the customer pays $2.70 less.

This test pins the user's exact scenario.  After fix:
  - SNAP row labels: Match=$66.00 + give-back $2.70 = $68.70,
    Total=$245.50 (method unchanged, customer drops $2.70)
  - FB Healthy: Match=$11.30 (forfeit reduction), Total=$25.30
  - FAM Match card: $100.00
  - Customer Pays card: $210.80 (down from $213.50)
  - Saved DB rows (Confirm path):
      v1: FB Fudgie  $40.00 / $20.00 / $20.00
      v2: FB Healthy $25.30 / $11.30 / $14.00 ← customer preserved!
      v3: SNAP       $120.50 / $33.72 / $86.78
      v4: SNAP       $125.00 / $34.98 / $90.02
    Total: method=$310.80, match=$100.00, customer=$210.80
    Per-vendor reconciliation: exact to the cent.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def four_vendor_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "cap_giveback.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    for vid, name in [(1, 'Fudgie'), (2, 'Healthy'),
                       (3, "Jill's"), (4, 'Kizzle')]:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))
    methods = [
        (1, 'SNAP',          100.0, None, 1),
        (2, 'Cash',            0.0, None, 2),
        (4, 'JH Food Bucks', 100.0,  200, 4),
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
        for mid in (1, 2, 4):
            conn.execute(
                "INSERT INTO vendor_payment_methods "
                " (vendor_id, payment_method_id) VALUES (?, ?)",
                (vid, mid))
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-30', 'Open', 'T')")
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-001-LB1',
        zip_code='15102')
    for vid, receipt in [(1, 4000), (2, 2530),
                          (3, 12050), (4, 12500)]:
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


def _row_labels(row):
    return (row.match_amount_label.text(),
            row.total_label.text())


def _cards(screen):
    return {
        'allocated': screen.summary_row.cards['allocated'].value_label.text(),
        'customer_pays':
            screen.summary_row.cards['customer_pays'].value_label.text(),
        'fam_match': screen.summary_row.cards['fam_match'].value_label.text(),
        'remaining': screen.summary_row.cards['remaining'].value_label.text(),
    }


class TestCapAwareForfeitGiveBack:
    """Validates Bug #2 fix: when forfeit reduces denom match under
    an active cap, the freed cap capacity is given back to non-denom
    rows so FAM Match stays at the full cap and the customer benefits
    by paying the forfeit amount less."""

    def test_user_screenshot_auto_distribute(
            self, qtbot, four_vendor_db):
        """Auto-Distribute path: 10 FB Fudgie + 7 FB Healthy → SNAP
        auto-fills.  After Pass 4 give-back, FAM Match must equal the
        $100 cap and customer pays $210.80 (down from $213.50)."""
        from fam.ui.payment_screen import PaymentScreen
        conn, order_id = four_vendor_db

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add(screen, 'Food Bucks', 2000, vid=1)
        _add(screen, 'Food Bucks', 1400, vid=2)
        screen._auto_distribute()

        cards = _cards(screen)
        assert cards['fam_match'] == "$100.00", (
            f"FAM Match card should be $100.00 (full cap, give-back "
            f"covered the forfeit), got {cards['fam_match']}")
        assert cards['customer_pays'] == "$210.80", (
            f"Customer Pays card should be $210.80 (= $213.50 − "
            f"$2.70 give-back), got {cards['customer_pays']}")
        assert cards['allocated'] == "$313.50", (
            f"Allocated should reflect the $2.70 denom over-allocation, "
            f"got {cards['allocated']}")

    def test_snap_row_labels_capped_not_uncapped(
            self, qtbot, four_vendor_db):
        """Bug #1 regression: SNAP row labels must show the engine's
        capped values, NOT the spinbox-derived ``charge × pct`` formula
        (which would show Match=$179.50 / Total=$359.00 — exceeding
        the order total)."""
        from fam.ui.payment_screen import PaymentScreen
        conn, order_id = four_vendor_db

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add(screen, 'Food Bucks', 2000, vid=1)
        _add(screen, 'Food Bucks', 1400, vid=2)
        screen._auto_distribute()

        # Find the SNAP row.
        snap_row = next(
            r for r in screen._payment_rows
            if r.get_selected_method()
            and r.get_selected_method()['name'] == 'SNAP'
        )
        match_lbl, total_lbl = _row_labels(snap_row)
        assert match_lbl == "$68.70", (
            f"SNAP Match label must be $68.70 (= $66 cap-share "
            f"+ $2.70 give-back), got {match_lbl}.  If $179.50, "
            f"_recompute() is overwriting set_display_values().")
        assert total_lbl == "$245.50", (
            f"SNAP Total label must be $245.50 (method_amount), "
            f"got {total_lbl}.  If $359.00, the row is showing "
            f"the uncapped charge × (1 + pct/100) formula.")

    def test_fb_healthy_row_labels_post_forfeit(
            self, qtbot, four_vendor_db):
        """FB Healthy row labels must reflect the post-forfeit values
        ($11.30 match, $25.30 total), NOT pre-forfeit ($14 / $28)."""
        from fam.ui.payment_screen import PaymentScreen
        conn, order_id = four_vendor_db

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add(screen, 'Food Bucks', 2000, vid=1)
        _add(screen, 'Food Bucks', 1400, vid=2)
        screen._auto_distribute()

        # FB row bound to vendor 2 (Healthy) — the over-allocated one.
        healthy_row = next(
            r for r in screen._payment_rows
            if r.get_bound_vendor_id() == 2
        )
        match_lbl, total_lbl = _row_labels(healthy_row)
        assert match_lbl == "$11.30", (
            f"FB Healthy Match must show post-forfeit $11.30, "
            f"got {match_lbl}.  If $14.00, label was set before "
            f"forfeit ran.")
        assert total_lbl == "$25.30", (
            f"FB Healthy Total must show post-forfeit $25.30 "
            f"(= vendor 2 receipt), got {total_lbl}.")

    def test_db_save_preserves_customer_charged_after_confirm(
            self, qtbot, four_vendor_db, monkeypatch):
        """After Confirm, the saved DB rows must have:
        - FB Healthy customer_charged = $14 (= 7 × $2 tokens, NOT
          $12.65 from the broken formula recompute)
        - Per-vendor allocation exactly = each vendor's receipt
        - Total match = $100 (cap), customer = $210.80
        """
        from fam.ui.payment_screen import PaymentScreen
        from PySide6.QtWidgets import QDialog
        import fam.ui.widgets.payment_confirmation_dialog as pcd

        conn, order_id = four_vendor_db

        # Stub PaymentConfirmationDialog to auto-accept.
        def stub_init(self, *args, **kwargs):
            QDialog.__init__(self)
        def stub_exec(self):
            return QDialog.Accepted
        monkeypatch.setattr(
            pcd.PaymentConfirmationDialog, '__init__', stub_init)
        monkeypatch.setattr(
            pcd.PaymentConfirmationDialog, 'exec', stub_exec)

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add(screen, 'Food Bucks', 2000, vid=1)
        _add(screen, 'Food Bucks', 1400, vid=2)
        screen._auto_distribute()
        screen._confirm_payment()

        rows = conn.execute(
            "SELECT t.vendor_id, pli.method_name_snapshot, "
            " pli.method_amount, pli.match_amount, pli.customer_charged "
            "FROM payment_line_items pli "
            "JOIN transactions t ON pli.transaction_id = t.id "
            "WHERE t.customer_order_id=? ORDER BY t.vendor_id",
            (order_id,)).fetchall()

        # Collect FB Healthy values for vendor 2.
        healthy_rows = [r for r in rows
                        if r[0] == 2 and r[1] == 'JH Food Bucks']
        assert len(healthy_rows) == 1, (
            f"Should be exactly one FB row on vendor 2, got "
            f"{len(healthy_rows)}: {healthy_rows}")
        h = healthy_rows[0]
        assert h[2] == 2530, (
            f"FB Healthy method_amount must equal vendor 2 "
            f"receipt $25.30, got ${h[2]/100:.2f}")
        assert h[3] == 1130, (
            f"FB Healthy match must equal $11.30 (post-forfeit), "
            f"got ${h[3]/100:.2f}")
        assert h[4] == 1400, (
            f"FB Healthy customer_charged MUST equal $14.00 "
            f"(7 × $2 tokens — what the customer physically handed "
            f"over).  Got ${h[4]/100:.2f}.  If $12.65, save path "
            f"Phase 1 is recomputing match from method × pct/(100+pct) "
            f"formula and silently undoing the forfeit reduction.")

        # Per-vendor reconciliation
        for vid in (1, 2, 3, 4):
            receipt = conn.execute(
                "SELECT receipt_total FROM transactions "
                "WHERE vendor_id=? AND customer_order_id=?",
                (vid, order_id)).fetchone()[0]
            alloc = sum(r[2] for r in rows if r[0] == vid)
            assert alloc == receipt, (
                f"Vendor {vid}: alloc={alloc}c != receipt={receipt}c")

        # Totals
        total_method = sum(r[2] for r in rows)
        total_match = sum(r[3] for r in rows)
        total_customer = sum(r[4] for r in rows)
        assert total_method == 31080, (
            f"Total method must = $310.80, got ${total_method/100:.2f}")
        assert total_match == 10000, (
            f"Total match must = $100 (full cap), "
            f"got ${total_match/100:.2f}")
        assert total_customer == 21080, (
            f"Total customer must = $210.80 (= $213.50 − $2.70 "
            f"give-back), got ${total_customer/100:.2f}")

    def test_no_cap_no_giveback(
            self, qtbot, four_vendor_db):
        """Sanity: when cap is NOT active (or denom uncapped > cap),
        give-back doesn't fire — current forfeit semantics preserved
        (customer pays unchanged amount, FAM match drops by forfeit)."""
        from fam.ui.payment_screen import PaymentScreen

        conn, order_id = four_vendor_db
        # Disable match limit on this test's market.
        conn.execute(
            "UPDATE markets SET match_limit_active=0 WHERE id=1")
        conn.commit()

        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add(screen, 'Food Bucks', 2000, vid=1)
        _add(screen, 'Food Bucks', 1400, vid=2)
        screen._auto_distribute()

        # Without cap, denom forfeit reduces denom match — no give-back.
        cards = _cards(screen)
        # FAM Match should be uncapped uncapped (not pinned to $100).
        # Specifically: $20 + $11.30 + (SNAP match) = match total.
        # SNAP customer = $310.80 - $40 - $25.30 = $245.50 → SNAP
        # match = $245.50/2 = $122.75; SNAP customer = $122.75.
        # Total: $20 + $11.30 + $122.75 = $154.05.
        assert cards['fam_match'] == "$154.05", (
            f"Without cap: FAM Match should be $154.05 (denom "
            f"forfeit reduces match by $2.70, no give-back), "
            f"got {cards['fam_match']}")
