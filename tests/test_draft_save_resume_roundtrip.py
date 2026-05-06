"""Regression: draft save + resume must produce identical state.

User-reported scenario: 4-vendor order, 2 FB rows + SNAP, save as
draft, resume → totals all wrong.

Three bugs discovered together:
  1. ``_distribute_and_save_payments`` had a parallel cap-logic
     implementation (separate from ``calculate_payment_breakdown``)
     that did naive proportional reduction inflating denom
     customer_charged → wrong values saved to DB.
  2. ``PaymentRow.set_data`` set the charge BEFORE binding the
     vendor.  During restore, ``_update_summary`` fired after the
     method-combo change with the row unbound, computed max=0 (the
     non-denom-row else-branch saw uncapped SNAP method exceeding
     order capacity), and the charge write was silently clamped to 0.
  3. ``_group_saved_line_items_for_restore`` returned non-denom rows
     first, then denom.  ``_update_summary``'s ``running_alloc`` cap
     gave the first non-denom row the FULL effective_total.  Denom
     rows added on top → total method = receipt + denom worth (e.g.
     $381.50 on a $310.80 order).

This test pins the round-trip contract: pre-draft cards == post-
resume cards == post-resume DB.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def four_vendor_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "draft.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    for vid, name in [(1, 'F'), (2, 'H'), (3, 'J'), (4, 'K')]:
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
        " opened_by) VALUES (1, 1, '2026-04-29', 'Open', 'T')")
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
            market_day_date='2026-04-29')
    conn.commit()
    from PySide6.QtWidgets import QApplication, QMessageBox
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


def _cards_snapshot(screen):
    """Capture all 4 summary card values."""
    return {
        'allocated': screen.summary_row.cards['allocated'].value_label.text(),
        'customer_pays': screen.summary_row.cards['customer_pays'].value_label.text(),
        'fam_match': screen.summary_row.cards['fam_match'].value_label.text(),
        'remaining': screen.summary_row.cards['remaining'].value_label.text(),
    }


class TestDraftSaveResumeRoundTrip:
    """Pre-draft and post-resume state must be byte-identical at the
    summary-card level and at the per-row charge level."""

    def test_user_screenshot_scenario_round_trip(
            self, qtbot, four_vendor_db):
        from fam.ui.payment_screen import PaymentScreen
        conn, order_id = four_vendor_db

        # Pre-draft setup matching the screenshot.
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
        _add(screen, 'SNAP', 17950)
        screen._update_summary()

        pre_draft_cards = _cards_snapshot(screen)
        pre_draft_charges = sorted(
            (r.get_selected_method()['name'],
             r._get_active_charge(),
             r.get_bound_vendor_id())
            for r in screen._payment_rows
            if r.get_selected_method()
        )

        # Save draft.
        screen._save_draft()

        # Verify saved DB rows have correct denom customer_charged
        # (the ``_distribute_and_save_payments`` cap fix).
        rows = conn.execute(
            "SELECT t.vendor_id, pli.method_name_snapshot, "
            " pli.method_amount, pli.match_amount, pli.customer_charged "
            "FROM payment_line_items pli "
            "JOIN transactions t ON pli.transaction_id = t.id "
            "WHERE t.customer_order_id=? "
            "ORDER BY t.vendor_id",
            (order_id,)).fetchall()
        # FB Fudgie: customer should be exactly 10×$2 = 2000c.
        # FB Healthy: customer should be exactly 7×$2 = 1400c.
        # NOT cap-inflated to 2724 / 1907 (the v1.9.10 bug).
        for r in rows:
            if r[1] == 'JH Food Bucks':
                if r[0] == 1:  # Fudgie
                    assert r[4] == 2000, (
                        f"FB Fudgie customer_charged should be 2000c "
                        f"(10 units × $2), got {r[4]}c — save-path "
                        "cap is inflating denom rows.")
                elif r[0] == 2:  # Healthy
                    assert r[4] == 1400, (
                        f"FB Healthy customer_charged should be 1400c "
                        f"(7 units × $2), got {r[4]}c.")

        # Resume on a fresh PaymentScreen.
        screen2 = PaymentScreen()
        qtbot.addWidget(screen2)
        screen2.load_customer_order(order_id)

        # Cards must match pre-draft exactly.
        post_resume_cards = _cards_snapshot(screen2)
        assert post_resume_cards == pre_draft_cards, (
            f"Draft round-trip drift in summary cards:\n"
            f"  pre-draft:    {pre_draft_cards}\n"
            f"  post-resume:  {post_resume_cards}")

        # Per-row charges must match pre-draft (order may differ).
        post_resume_charges = sorted(
            (r.get_selected_method()['name'],
             r._get_active_charge(),
             r.get_bound_vendor_id())
            for r in screen2._payment_rows
            if r.get_selected_method()
        )
        assert post_resume_charges == pre_draft_charges, (
            f"Draft round-trip drift in row charges:\n"
            f"  pre-draft:    {pre_draft_charges}\n"
            f"  post-resume:  {post_resume_charges}")

    def test_no_error_after_resume(self, qtbot, four_vendor_db):
        """The screenshot showed 'Total allocated does not match
        receipt total'.  After fix, error label must be empty."""
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
        _add(screen, 'SNAP', 17950)
        screen._update_summary()
        screen._save_draft()

        screen2 = PaymentScreen()
        qtbot.addWidget(screen2)
        screen2.load_customer_order(order_id)
        err_text = screen2.error_label.text()
        assert 'does not match' not in err_text.lower(), (
            f"Resume produced reconciliation error: {err_text!r}")
