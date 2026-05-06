"""Vendor reimbursement: Unallocated Funds column shows the
absorbed amount, not customer_charged (v1.9.10 follow-up,
2026-05-01).

Onsite report: a manager adjusted Healthy Heartbeets' receipt from
$32.56 → $42.56 with the customer-gone branch (so the $10 gap was
absorbed as Unallocated Funds).  The FAM Absorbed summary card
correctly showed $10, but the Vendor Reimbursement table's
``Unallocated Funds`` column for that vendor's row showed **$0**.

That broke the row identity:

    Σ per-method-cols + FAM Match + FMNP_External  ==  Total Due

For Healthy Heartbeets the table read:

    SNAP $16.28 + UF $0 + FAM Match $16.28 + FMNP $0  =  $32.56

…but Total Due to Vendor was $42.56 — the column visually missed
$10 of payment-to-vendor obligation.

Root cause: the per-method query SUMs ``customer_charged`` for every
method, displays that as the column value.  Unallocated Funds rows
intentionally have ``customer_charged = 0`` (the customer didn't
hand it over — FAM absorbed it); the displayed column for UF is
therefore always 0 even when method_amount > 0.

Fix: for the system-managed UF method specifically, the per-method
column shows ``method_amount`` (the absorbed loss).  Every other
method continues to show customer_charged (the redeemable scrip
total the vendor needs to count).  Applied in both
``fam/sync/data_collector.py::_collect_vendor_reimbursement``
(cloud sheet) and ``fam/ui/reports_screen.py`` (in-app table).

After the fix the row reads:

    SNAP $16.28 + UF $10.00 + FAM Match $16.28 + FMNP $0  =  $42.56
"""

import pytest

from fam.database.connection import (
    get_connection, set_db_path, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def db(tmp_path):
    db_file = str(tmp_path / "uf_reimb.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'Healthy Heartbeets')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "(1, 'SNAP', 100.0, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO vendor_payment_methods (vendor_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES (1, 1, '2026-05-01', 'Open', 'Tester')")
    conn.commit()
    yield conn
    close_connection()


def _build_customer_gone_scenario(db, original_cents=3256, new_cents=4256):
    """Confirm a SNAP txn for ``original_cents``, then "adjust"
    it (mirroring the admin save flow) so the receipt rises to
    ``new_cents`` with the gap absorbed as Unallocated Funds.

    Returns the absorbed gap in cents.
    """
    from fam.models.transaction import (
        create_transaction, save_payment_line_items,
        confirm_transaction, update_transaction,
    )
    from fam.ui.admin_screen import _append_unallocated_funds_row

    txn_id, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=original_cents,
        market_day_date='2026-05-01')
    cust = original_cents // 2
    match = original_cents - cust
    items = [{
        'payment_method_id': 1,
        'method_name_snapshot': 'SNAP',
        'match_percent_snapshot': 100.0,
        'method_amount': original_cents,
        'match_amount': match,
        'customer_charged': cust,
        'photo_path': None,
    }]
    save_payment_line_items(txn_id, items)
    confirm_transaction(txn_id, confirmed_by='Tester')

    # Mirror the customer-gone branch in admin_screen.
    update_transaction(
        txn_id, receipt_total=new_cents, status='Adjusted',
        changed_by='Tester')
    gap = new_cents - original_cents
    seeded = _append_unallocated_funds_row(items, gap)
    assert seeded is not None
    save_payment_line_items(txn_id, items)
    return gap


# ════════════════════════════════════════════════════════════════════
# 1. Cloud-sync vendor reimbursement (data_collector.py)
# ════════════════════════════════════════════════════════════════════


class TestCollectorShowsAbsorbedInUFColumn:

    def test_uf_column_shows_absorbed_method_amount(self, db):
        """The user's exact scenario — $32.56 → $42.56 with
        customer-gone — must surface $10.00 in the
        Unallocated Funds column for Healthy Heartbeets so the
        row balances to Total Due."""
        from fam.sync.data_collector import _collect_vendor_reimbursement

        _build_customer_gone_scenario(db)

        rows = _collect_vendor_reimbursement(db, [1])
        target = next(
            r for r in rows if r['Vendor'] == 'Healthy Heartbeets')

        # Total Due = receipt_total ($42.56)
        assert abs(target['Total Due to Vendor'] - 42.56) < 1e-9

        # Unallocated Funds column = absorbed loss ($10.00),
        # NOT customer_charged ($0).
        assert 'Unallocated Funds' in target, (
            "Vendor Reimbursement row must include an "
            "'Unallocated Funds' column for vendors whose "
            "transactions were customer-gone-adjusted")
        assert abs(target['Unallocated Funds'] - 10.00) < 1e-9, (
            f"Unallocated Funds column must show the absorbed "
            f"amount ($10.00); got "
            f"${target['Unallocated Funds']:.2f}.  Pre-fix this "
            f"showed $0 because the column SUMs customer_charged.")

    def test_row_identity_holds_after_uf_fix(self, db):
        """After the fix, the report invariant holds in cents:

            Σ method-cols + FAM Match + FMNP_External == Total Due
        """
        from fam.sync.data_collector import _collect_vendor_reimbursement
        _build_customer_gone_scenario(db)

        rows = _collect_vendor_reimbursement(db, [1])
        r = next(x for x in rows if x['Vendor'] == 'Healthy Heartbeets')

        method_sum_cents = sum(
            round(r.get(m, 0) * 100)
            for m in ('SNAP', 'Unallocated Funds')
        )
        fam_match_cents = round(r['FAM Match'] * 100)
        fmnp_cents = round(r['FMNP (External)'] * 100)
        total_due_cents = round(r['Total Due to Vendor'] * 100)

        assert method_sum_cents + fam_match_cents + fmnp_cents == total_due_cents, (
            f"Row identity violated: SNAP({r.get('SNAP', 0):.2f}) + "
            f"UF({r.get('Unallocated Funds', 0):.2f}) + "
            f"FAM Match({r['FAM Match']:.2f}) + "
            f"FMNP({r['FMNP (External)']:.2f}) "
            f"!= Total Due({r['Total Due to Vendor']:.2f})"
        )

    def test_ordinary_method_column_still_shows_customer_charged(self, db):
        """Regression guard: only the UF column was changed; every
        other method's column still shows customer_charged (the
        physical-instrument total the vendor redeems)."""
        from fam.sync.data_collector import _collect_vendor_reimbursement
        _build_customer_gone_scenario(db)

        rows = _collect_vendor_reimbursement(db, [1])
        r = next(x for x in rows if x['Vendor'] == 'Healthy Heartbeets')

        # Customer paid $16.28 SNAP (half of original $32.56).  The
        # SNAP column must show that, NOT the SNAP method_amount
        # ($32.56).
        assert abs(r['SNAP'] - 16.28) < 1e-9, (
            f"SNAP column must show customer_charged ($16.28), "
            f"got ${r['SNAP']:.2f}")


# ════════════════════════════════════════════════════════════════════
# 2. In-app reports screen mirrors the same logic
# ════════════════════════════════════════════════════════════════════


class TestInAppReportsMirrorsCollectorFix:

    def test_in_app_method_query_includes_method_total(self):
        """Source-level pin: the in-app reports query must SELECT
        ``method_amount`` so the UF-column override has the data
        it needs.  A future regression that drops the column from
        the SELECT will fail this test loud."""
        import inspect
        import fam.ui.reports_screen as rs
        src = inspect.getsource(rs)
        # The query selects method_total alongside customer_total.
        assert 'COALESCE(SUM(pl.method_amount), 0) AS method_total' in src, (
            "Reports screen vendor-reimbursement query must SELECT "
            "method_amount so the UF column override works")

    def test_in_app_uses_uf_branch_in_method_aggregation(self):
        """The aggregation loop must branch on UNALLOCATED_FUNDS_NAME
        so the UF row gets method_total instead of customer_total."""
        import inspect
        import fam.ui.reports_screen as rs
        src = inspect.getsource(rs)
        assert 'UNALLOCATED_FUNDS_NAME' in src, (
            "Reports screen must import UNALLOCATED_FUNDS_NAME for "
            "the per-method column branch")
        assert "if r['method'] == UNALLOCATED_FUNDS_NAME" in src, (
            "Reports screen must branch on the UF method name when "
            "filling the per-method column")
