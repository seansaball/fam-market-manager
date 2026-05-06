"""Regression: row visual order must NOT affect cap allocation.

User-reported (2026-04-30 onsite, customer C-003-LB1):

  Order $211.91 across 5 vendors.  Daily cap $100.

  Volunteer adds:
    - SNAP $106.80 (manually typed, FIRST)
    - 3 × JH Food Bucks → Haffey Family Farm (denom, SECOND)

  Expected: live screen shows the same totals it would after a
  save + resume round-trip (Allocated $212.80, FAM Match $100,
  Customer Pays $111.91, Remaining -$0.89 denom-forfeit).

  Observed: Allocated $224.80, Customer Pays $124.80, FAM Match
  $100, Remaining -$12.89.  Multiple negative per-vendor remainders
  on the breakdown table.  Error label fires:
    "Total allocated ($224.80) does not match receipt total
    ($211.91).  Remaining: $-12.89."

  Workaround the user found: Save as Draft and resume — that round-
  trip "magically" fixes it.

Root cause
----------
``_update_summary`` and ``_collect_line_items`` cap each non-denom
row at ``effective_total - running_alloc``.  ``running_alloc`` is
accumulated as the loop iterates, so when a non-denom row is
processed BEFORE its sibling denom row(s), the cap doesn't yet
include the denom row's contribution.  SNAP gets the full
``effective_total`` budget, then the trailing FB row pushes the
allocation over the receipt by exactly the denom method_amount.

Save + resume "fixes" it because
``_group_saved_line_items_for_restore`` (post-v1.9.10) returns
denom_groups FIRST then non_denom_groups — the round-trip reorders
rows into the order ``running_alloc`` happens to expect.

Fix
---
Pre-sum total denom method_amount BEFORE iterating, then cap
non-denom rows at ``effective_total - total_denom - non_denom_running``.
This is order-independent: visual row order doesn't affect the cap.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def five_vendor_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "row_order.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    for vid, name in [(1, 'Juice'), (2, 'Haffey'),
                       (3, 'Rockin'), (4, 'Cakery'),
                       (5, 'Olive')]:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))
    for mid, name, pct, denom, sort_o in [
            (1, 'SNAP',          100.0, None, 1),
            (2, 'Cash',            0.0, None, 2),
            (4, 'JH Food Bucks', 100.0,  200, 4)]:
        conn.execute(
            "INSERT INTO payment_methods (id, name, "
            " match_percent, denomination, sort_order, "
            " is_active) VALUES (?, ?, ?, ?, ?, 1)",
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
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction
    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-003-LB1',
        zip_code='15102')
    # Receipts from the screenshot.
    for vid, receipt in [(1, 4523), (2, 1111),
                          (3, 4565), (4, 8536), (5, 2456)]:
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


def _cards(screen):
    return {
        'allocated':
            screen.summary_row.cards['allocated'].value_label.text(),
        'customer_pays':
            screen.summary_row.cards['customer_pays'].value_label.text(),
        'fam_match':
            screen.summary_row.cards['fam_match'].value_label.text(),
        'remaining':
            screen.summary_row.cards['remaining'].value_label.text(),
    }


class TestRowOrderIndependence:
    """The cap allocation must NOT depend on the visual order of
    rows.  SNAP-first vs FB-first must produce identical summary
    cards and per-vendor remainders."""

    def test_snap_first_then_fb_matches_fb_first_then_snap(
            self, qtbot, five_vendor_db):
        """The 2026-04-30 onsite scenario: 3 FB on Haffey + SNAP
        $106.80, total cap = $100.  Whether SNAP or FB is added
        first, the screen state must converge on the same totals."""
        from fam.ui.payment_screen import PaymentScreen

        conn, order_id = five_vendor_db

        # Variant A: SNAP first, then FB.
        screen_a = PaymentScreen()
        qtbot.addWidget(screen_a)
        screen_a.load_customer_order(order_id)
        while screen_a._payment_rows:
            r = screen_a._payment_rows[0]
            screen_a.rows_layout.removeWidget(r)
            r.deleteLater()
            screen_a._payment_rows.remove(r)
        _add(screen_a, 'SNAP', 10680)
        _add(screen_a, 'Food Bucks', 600, vid=2)
        screen_a._update_summary()

        # Variant B: FB first, then SNAP.
        screen_b = PaymentScreen()
        qtbot.addWidget(screen_b)
        screen_b.load_customer_order(order_id)
        while screen_b._payment_rows:
            r = screen_b._payment_rows[0]
            screen_b.rows_layout.removeWidget(r)
            r.deleteLater()
            screen_b._payment_rows.remove(r)
        _add(screen_b, 'Food Bucks', 600, vid=2)
        _add(screen_b, 'SNAP', 10680)
        screen_b._update_summary()

        cards_a = _cards(screen_a)
        cards_b = _cards(screen_b)
        assert cards_a == cards_b, (
            f"Row order changed summary cards:\n"
            f"  SNAP-first: {cards_a}\n"
            f"  FB-first:   {cards_b}")

        # And the values themselves must be the post-fix values
        # (not the pre-fix bad state of $224.80 / $124.80).
        assert cards_a['allocated'] == "$212.80", (
            f"Allocated must equal effective_order_total ($12 FB "
            f"+ $200.80 non-denom-needed = $212.80), got "
            f"{cards_a['allocated']}.  If $224.80, the cap was "
            f"computed without the denom contribution — see the "
            f"_update_summary cap order fix.")
        assert cards_a['customer_pays'] == "$111.91"
        assert cards_a['fam_match'] == "$100.00"
        assert cards_a['remaining'] == "$-0.89"

    def test_no_per_vendor_negatives_in_snap_first_order(
            self, qtbot, five_vendor_db):
        """The user observed negative per-vendor remainders on the
        breakdown table when SNAP was added first.  After fix, the
        breakdown must show $0.00 remaining on every vendor."""
        from fam.ui.payment_screen import PaymentScreen

        conn, order_id = five_vendor_db
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)
        while screen._payment_rows:
            r = screen._payment_rows[0]
            screen.rows_layout.removeWidget(r)
            r.deleteLater()
            screen._payment_rows.remove(r)

        _add(screen, 'SNAP', 10680)
        _add(screen, 'Food Bucks', 600, vid=2)
        screen._update_summary()

        table = screen.vendor_table
        for r in range(table.rowCount()):
            rem_item = table.item(r, 2)
            if rem_item is None:
                continue
            text = rem_item.text()
            assert '-' not in text, (
                f"Vendor row {r} has negative remaining: {text!r}.  "
                f"This is the visible manifestation of the cap-order "
                f"bug.")

    def test_collect_line_items_order_independent(
            self, qtbot, five_vendor_db):
        """The save/confirm path's ``_collect_line_items`` uses the
        same cap.  Whether visual order is SNAP-first or FB-first,
        the items list must produce a per-vendor allocation that
        reconciles exactly to each vendor's receipt."""
        from fam.ui.payment_screen import PaymentScreen
        from fam.utils.calculations import (
            calculate_payment_breakdown,
        )

        conn, order_id = five_vendor_db

        for ordering in ('snap_first', 'fb_first'):
            screen = PaymentScreen()
            qtbot.addWidget(screen)
            screen.load_customer_order(order_id)
            while screen._payment_rows:
                r = screen._payment_rows[0]
                screen.rows_layout.removeWidget(r)
                r.deleteLater()
                screen._payment_rows.remove(r)
            if ordering == 'snap_first':
                _add(screen, 'SNAP', 10680)
                _add(screen, 'Food Bucks', 600, vid=2)
            else:
                _add(screen, 'Food Bucks', 600, vid=2)
                _add(screen, 'SNAP', 10680)
            screen._update_summary()

            items = screen._collect_line_items()
            allocated_total = sum(it['method_amount'] for it in items)

            assert allocated_total == 21280, (
                f"{ordering}: _collect_line_items returned "
                f"allocated={allocated_total}c, expected 21280c "
                f"($212.80).  If 22480c the cap is order-dependent.")
