"""Regression: vendor reimbursement report under denomination
overage — vendor must receive the FULL receipt total even when
FAM match was forfeit-reduced.

User's exact question (2026-04-30):

    "If a customer has one vendor receipt for $3 and wants to pay
     with a single foodbuck for $2, FAM matches $2, but there is
     a $1 overage, the vendor report shows $3 which includes the
     full foodbuck plus the match right?"

Pinned answer: YES.

  Receipt:                $3.00
  Customer pays:          $2.00 (1 × $2 FB token, FIXED)
  FAM match (uncapped):   $2.00 (100% match)
  Overage (forfeit):      $1.00 (would push allocation to $4 > $3)
  FAM match (forfeit):    $1.00 (reduced)
  Method total per row:   $3.00 (= customer + match)
  Vendor reimbursement:   $3.00 ← matches receipt exactly

The vendor sees the FULL $3 in the "Total Due to Vendor" column
of the Vendor Reimbursement report.  The $1 of "lost" match is a
FAM-side savings (FAM only pays $1 instead of $2 because the
customer's denominated token over-paid the receipt).

Per-line invariant on the saved row:
  customer_charged($2) + match_amount($1) = method_amount($3) ✓
"""
import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def overage_db(tmp_path, monkeypatch):
    """Set up the user's exact scenario: $3 receipt, FB $2 denom."""
    from fam.models.customer_order import (
        create_customer_order, update_customer_order_status,
    )
    from fam.models.transaction import (
        create_transaction, confirm_transaction,
    )
    db_file = str(tmp_path / "overage.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'Test Market', 10000, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'V1')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " denomination, sort_order, is_active) "
        "VALUES (4, 'JH Food Bucks', 100.0, 200, 4, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        " payment_method_id) VALUES (1, 4)")
    conn.execute(
        "INSERT INTO vendor_payment_methods "
        "(vendor_id, payment_method_id) VALUES (1, 4)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-30', 'Open', 'T')")

    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-OV',
        zip_code='15102')
    create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=300,
        customer_order_id=order_id,
        market_day_date='2026-04-30')
    conn.commit()

    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, 'question',
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.No))
    yield conn, order_id
    close_connection()


def _confirm_with_fb_two_dollars(qtbot, order_id, monkeypatch):
    """User flow: open PaymentScreen, drop a single FB token ($2),
    confirm → forfeit reduces match by $1 to fit $3 receipt."""
    from PySide6.QtWidgets import QDialog
    import fam.ui.widgets.payment_confirmation_dialog as pcd
    from fam.ui.payment_screen import PaymentScreen

    screen = PaymentScreen()
    qtbot.addWidget(screen)
    screen.load_customer_order(order_id)
    while screen._payment_rows:
        r = screen._payment_rows[0]
        screen.rows_layout.removeWidget(r)
        r.deleteLater()
        screen._payment_rows.remove(r)

    row = screen._add_payment_row()
    for i in range(row.method_combo.count()):
        d = row.method_combo.itemData(i)
        if d and d.get('id') == 4:  # FB
            row.method_combo.setCurrentIndex(i)
            break
    row.set_bound_vendor_id(1)
    row._set_active_charge(200)  # $2 = 1 FB token
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
    return screen


class TestVendorReimbursementUnderDenomOverage:

    def test_saved_line_item_has_full_method_amount(
            self, qtbot, overage_db, monkeypatch):
        """The saved payment_line_item row's method_amount must
        equal the receipt total ($3) — the customer's $2 token
        plus FAM's $1 match (post-forfeit reduction)."""
        conn, order_id = overage_db
        _confirm_with_fb_two_dollars(qtbot, order_id, monkeypatch)
        rows = conn.execute(
            """SELECT method_name_snapshot, method_amount,
                      match_amount, customer_charged
               FROM payment_line_items pli
               JOIN transactions t ON pli.transaction_id = t.id
               WHERE t.customer_order_id = ?""",
            (order_id,)).fetchall()
        assert len(rows) == 1
        method_name, method, match, customer = rows[0]
        assert method_name == 'JH Food Bucks'
        assert customer == 200, (
            f"Customer charged must equal $2 (1 × $2 FB token, "
            f"FIXED), got ${customer/100:.2f}")
        assert match == 100, (
            f"FAM match must be $1 after forfeit (uncapped $2 "
            f"reduced to $1 to fit $3 receipt), got ${match/100:.2f}")
        assert method == 300, (
            f"Method amount must equal receipt $3 (= $2 customer + "
            f"$1 match), got ${method/100:.2f}")
        # Per-line invariant.
        assert customer + match == method, (
            f"R1 violated: {customer} + {match} != {method}")

    def test_vendor_reimbursement_query_shows_full_receipt(
            self, qtbot, overage_db, monkeypatch):
        """The vendor reimbursement collector returns the FULL
        receipt total ($3) in 'Total Due to Vendor' regardless of
        forfeit on the line item.

        Column semantics (v1.9.10+):
          * Per-method column ('JH Food Bucks') = customer_charged
            ONLY (= $2, the physical token face value).
          * 'FAM Match' column = SUM(match_amount) across methods
            (= $1, post-forfeit).
          * 'Total Due to Vendor' = SUM(receipt_total) = $3.
          * Math: $2 (FB) + $1 (FAM Match) = $3 (Total Due) ✓
        """
        from fam.sync.data_collector import _collect_vendor_reimbursement

        conn, order_id = overage_db
        _confirm_with_fb_two_dollars(qtbot, order_id, monkeypatch)

        rows = _collect_vendor_reimbursement(conn, [1])
        assert len(rows) == 1
        row = rows[0]
        assert row['Vendor'] == 'V1'
        assert row['Total Due to Vendor'] == 3.00, (
            f"Vendor reimbursement must equal full $3 receipt, "
            f"got ${row['Total Due to Vendor']:.2f}.  This is what "
            f"FAM finance pays the vendor — the customer's $2 "
            f"physical token is included PLUS the $1 FAM match.")
        # Per-method column shows ONLY customer_charged (the
        # physical-instrument face value the vendor needs to
        # redeem) — NOT mixed with the match.
        fb_col = row.get('JH Food Bucks', 0)
        assert fb_col == 2.00, (
            f"JH Food Bucks column must show $2.00 (= customer_charged"
            f", the physical $2 token the vendor will redeem), "
            f"got ${fb_col:.2f}")
        # New FAM Match column carries the $1 match contribution.
        fam_match_col = row.get('FAM Match', 0)
        assert fam_match_col == 1.00, (
            f"FAM Match column must show $1.00 (= match_amount "
            f"post-forfeit), got ${fam_match_col:.2f}")
        # Math identity: per-method totals + FAM Match = Total Due.
        assert fb_col + fam_match_col == row['Total Due to Vendor'], (
            f"Math identity violated: ${fb_col:.2f} (FB) + "
            f"${fam_match_col:.2f} (FAM Match) != "
            f"${row['Total Due to Vendor']:.2f} (Total Due)")

    def test_fam_match_report_shows_post_forfeit_match(
            self, qtbot, overage_db, monkeypatch):
        """The FAM Match report row for FB shows match=$1 (the
        forfeit-reduced amount FAM actually paid), not the
        uncapped $2."""
        from fam.sync.data_collector import _collect_fam_match

        conn, order_id = overage_db
        _confirm_with_fb_two_dollars(qtbot, order_id, monkeypatch)

        rows = _collect_fam_match(conn, 1)
        # Find the FB row.
        fb_row = next(
            (r for r in rows
             if r.get('Payment Method') == 'JH Food Bucks'),
            None)
        assert fb_row is not None, (
            f"FAM Match report missing FB row; got rows: {rows}")
        # Match was $2 uncapped → $1 after forfeit.
        assert fb_row['Total FAM Match'] == 1.00, (
            f"FAM Match column must show $1 (forfeit-reduced from "
            f"$2 uncapped), got ${fb_row['Total FAM Match']:.2f}.  "
            f"The other $1 is FAM-side savings, not vendor-side.")
        # Total Allocated = method_amount = $3 (the full vendor
        # reimbursement — customer + post-forfeit match).
        assert fb_row['Total Allocated'] == 3.00, (
            f"Total Allocated must show $3 (= post-forfeit "
            f"method_amount), got ${fb_row['Total Allocated']:.2f}")

    def test_vendor_reimbursement_total_matches_receipt(
            self, qtbot, overage_db, monkeypatch):
        """Σ(receipt_total) == Σ(method_amount) for any single
        confirmed transaction.  This is the per-vendor invariant
        the user asked about."""
        conn, order_id = overage_db
        _confirm_with_fb_two_dollars(qtbot, order_id, monkeypatch)
        row = conn.execute(
            """SELECT t.receipt_total,
                      COALESCE(SUM(pli.method_amount), 0) as alloc
               FROM transactions t
               LEFT JOIN payment_line_items pli
                 ON pli.transaction_id = t.id
               WHERE t.customer_order_id = ?
                 AND t.status IN ('Confirmed', 'Adjusted')
               GROUP BY t.id""",
            (order_id,)).fetchone()
        receipt, alloc = row
        assert receipt == alloc, (
            f"Receipt {receipt}c != allocation {alloc}c.  Vendor "
            f"reimbursement = receipt = {receipt}c.")
