"""Regression: Layer 2A guard blocks legitimate confirmation.

User-reported scenario (Customer C-001-LB1, 4 vendors, $310.80,
$100 cap):

  Fudgie Wudgie     $40.00
  Healthy Heartbeets $25.30
  Jill's gourmet dips $120.50
  KizzleFoods       $125.00

Volunteer enters:
  - 1 × JH Food Bucks @ Fudgie  ($2 customer)
  - 1 × JH Food Bucks @ Healthy ($2 customer)
  - SNAP charge $206.80 (set by Auto-Distribute or manual entry)

Then clicks Confirm Payment.  Layer 2A blocks::

    "Payment row mismatch detected and confirmation was blocked.
     The SNAP input shows $206.80 but the calculated charge after
     applying caps and reconciliation is $213.30."

Same pattern applies to FB rows: the engine produces non-denom-
multiple customer_charged values for denominated rows because of
the cap-aware proportional reduction on match.  The cap-write-back
path then can't update the spinbox cleanly (denom stepper rounds
to whole units), and Layer 2A blocks the mismatch.

This test pins the user's exact scenario.  After the fix, Confirm
Payment proceeds without Layer 2A firing.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def four_vendor_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "l2a.db")
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
        " opened_by) VALUES (1, 1, '2099-04-29', 'Open', 'T')")
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
            market_day_date='2099-04-29')
    conn.commit()
    # Stub Save Draft / Return-to-Intake popup so test stays headless.
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, 'question',
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.No))
    yield conn, order_id
    close_connection()


def _add_row(screen, method_sub, charge=0, vid=None):
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


class TestLayer2A_DoesNotBlockLegitimateConfirm:
    """The cap-aware engine produces customer_charged values that
    the cap-write-back path may not be able to mirror cleanly on
    denominated rows.  Layer 2A then blocks the user's confirm.

    These tests pin the user's exact reported scenarios.  After
    the fix, Confirm should proceed without Layer 2A firing OR
    the upstream code should produce row state consistent with
    the engine's customer_charged so 2A passes."""

    def test_user_screenshot_1_confirm_proceeds(
            self, qtbot, four_vendor_db):
        """Screenshot 1: 10 FB on Fudgie + 7 FB on Healthy +
        SNAP $167.19 → confirm should not be Layer 2A-blocked."""
        from fam.ui.payment_screen import PaymentScreen
        from PySide6.QtWidgets import QMessageBox

        conn, order_id = four_vendor_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        # Wipe blank.
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add_row(screen, 'Food Bucks', 2000, vid=1)  # 10 FB on Fudgie
        _add_row(screen, 'Food Bucks', 1400, vid=2)  # 7 FB on Healthy
        screen._auto_distribute()
        # After auto-distribute, all rows should be in confirm-ready
        # state.  Click Confirm.  If Layer 2A fires, the error label
        # becomes visible with text containing "row mismatch".
        screen._confirm_payment()

        # NOTE: ``error_label.isVisible()`` returns False in headless
        # qtbot mode even when the error text is set, because the
        # widget's parent window is never shown.  Check the text
        # content directly — that's what actually drives the
        # user-visible error message.
        err_text = screen.error_label.text()
        assert 'row mismatch' not in err_text.lower(), (
            f"Layer 2A blocked legitimate confirm.  Error shown:\n"
            f"{err_text}")

    def test_user_screenshot_2_confirm_proceeds(
            self, qtbot, four_vendor_db):
        """Screenshot 2: 1 FB on Fudgie + 1 FB on Healthy +
        SNAP $206.80 → confirm should not be Layer 2A-blocked."""
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

        _add_row(screen, 'Food Bucks', 200, vid=1)  # 1 FB on Fudgie
        _add_row(screen, 'Food Bucks', 200, vid=2)  # 1 FB on Healthy
        screen._auto_distribute()
        screen._confirm_payment()

        # NOTE: ``error_label.isVisible()`` returns False in headless
        # qtbot mode even when the error text is set, because the
        # widget's parent window is never shown.  Check the text
        # content directly — that's what actually drives the
        # user-visible error message.
        err_text = screen.error_label.text()
        assert 'row mismatch' not in err_text.lower(), (
            f"Layer 2A blocked legitimate confirm.  Error shown:\n"
            f"{err_text}")
