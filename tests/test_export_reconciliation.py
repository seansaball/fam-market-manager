"""Export ↔ DB reconciliation tests.

Every CSV export must agree to the cent with the underlying DB
ground truth.  These tests build a small market-day fixture, run
the export pipeline end-to-end (data_collector → export_*_csv),
and re-parse the CSVs to assert they reconcile against the DB.

Why this matters
----------------
Coordinators and FAM finance use the exports as the source of
truth for reimbursement.  A penny drift between the UI report,
the CSV, and the DB would silently cause a vendor to be over- or
under-paid.  These tests are the last line of defense.
"""

import csv
import os

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def export_db(tmp_path):
    """Small but financially-rich fixture: 3 vendors, 3 methods,
    10 confirmed transactions, plus 1 voided transaction."""
    db_file = str(tmp_path / "export.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'EM', 50000, 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-29', 'Open', 'T')")
    for vid, name in [(1, 'V1'), (2, 'V2'), (3, 'V3')]:
        conn.execute(
            "INSERT INTO vendors (id, name) VALUES (?, ?)",
            (vid, name))
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, ?)", (vid,))
    methods = [
        (1, 'SNAP', 100.0, None, 1),
        (2, 'Cash', 0.0, None, 2),
        (3, 'FMNP', 100.0, 500, 3),
    ]
    for mid, n, p, d, s in methods:
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            " denomination, sort_order, is_active) "
            "VALUES (?, ?, ?, ?, ?, 1)", (mid, n, p, d, s))
        conn.execute(
            "INSERT INTO market_payment_methods (market_id, "
            " payment_method_id) VALUES (1, ?)", (mid,))
        for vid in [1, 2, 3]:
            conn.execute(
                "INSERT INTO vendor_payment_methods "
                " (vendor_id, payment_method_id) VALUES (?, ?)",
                (vid, mid))
    conn.commit()

    # 10 confirmed transactions across 3 vendors with mixed methods.
    # Each receipt is a clean SNAP-only or SNAP+Cash split.
    from fam.models.customer_order import (
        create_customer_order, update_customer_order_status,
    )
    from fam.models.transaction import (
        create_transaction, save_payment_line_items,
        confirm_transaction, void_transaction,
    )
    seeds = [
        # (vendor_id, receipt_cents, [(method_id, name, pct, charge,
        #                              method_amt, match_amt), ...])
        (1, 2000, [(1, 'SNAP', 100.0, 1000, 2000, 1000)]),
        (1, 1000, [(2, 'Cash',   0.0, 1000, 1000,    0)]),
        (2, 3000, [(1, 'SNAP', 100.0, 1500, 3000, 1500)]),
        (2, 4000, [(1, 'SNAP', 100.0, 1000, 2000, 1000),
                   (2, 'Cash',   0.0, 2000, 2000,    0)]),
        (3, 5000, [(1, 'SNAP', 100.0, 2500, 5000, 2500)]),
        (3, 1500, [(3, 'FMNP', 100.0,  500, 1000,  500),
                   (2, 'Cash',   0.0,  500,  500,    0)]),
        (1, 2500, [(1, 'SNAP', 100.0, 1250, 2500, 1250)]),
        (2, 1234, [(2, 'Cash',   0.0, 1234, 1234,    0)]),
        (3, 2222, [(1, 'SNAP', 100.0, 1111, 2222, 1111)]),
        (1, 3333, [(1, 'SNAP', 100.0, 1666, 3332, 1666),
                   (2, 'Cash',   0.0,    1,    1,    0)]),
    ]
    for vid, receipt, plis in seeds:
        order_id, _ = create_customer_order(
            1, customer_label=f'C-{vid}-{receipt}')
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=vid,
            receipt_total=receipt, customer_order_id=order_id,
            market_day_date='2026-04-29')
        save_payment_line_items(tid, [{
            'payment_method_id': mid,
            'method_name_snapshot': name,
            'match_percent_snapshot': pct,
            'method_amount': method_amt,
            'match_amount': match_amt,
            'customer_charged': charge,
        } for mid, name, pct, charge, method_amt, match_amt
           in plis], commit=False)
        confirm_transaction(tid, confirmed_by='T', commit=False)
        update_customer_order_status(order_id, 'Confirmed',
                                      commit=False)
        conn.commit()

    # Add ONE voided transaction so the void-exclusion behavior of
    # exports is exercised.
    voided_order, _ = create_customer_order(1, customer_label='C-VOID')
    voided_tid, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=999,
        customer_order_id=voided_order,
        market_day_date='2026-04-29')
    save_payment_line_items(voided_tid, [{
        'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
        'match_percent_snapshot': 100.0, 'method_amount': 999,
        'match_amount': 499, 'customer_charged': 500,
    }], commit=False)
    confirm_transaction(voided_tid, confirmed_by='T', commit=False)
    update_customer_order_status(voided_order, 'Confirmed',
                                  commit=False)
    conn.commit()
    void_transaction(voided_tid, voided_by='T')

    yield conn
    close_connection()


# ══════════════════════════════════════════════════════════════════
# Vendor reimbursement: CSV export must equal DB ground truth
# ══════════════════════════════════════════════════════════════════

class TestVendorReimbursementExport:
    """The Vendor Reimbursement CSV is the document FAM finance
    pays from.  Penny drift = real money mistake."""

    def test_csv_matches_db_per_vendor(self, export_db, tmp_path):
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement,
        )
        from fam.utils.export import export_vendor_reimbursement
        rows = _collect_vendor_reimbursement(export_db, [1])
        out = str(tmp_path / 'vr.csv')
        export_vendor_reimbursement(rows, out)
        assert os.path.exists(out)

        # Re-parse the CSV and compute totals.
        with open(out, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            csv_rows = list(reader)

        # CSV total per vendor (in cents) must equal DB receipt total.
        for csv_row in csv_rows:
            vendor_name = csv_row['Vendor']
            csv_total = round(float(csv_row['Total Due to Vendor'])
                              * 100)
            # Resolve vendor_id from name and look up DB receipt.
            db_total = export_db.execute(
                "SELECT COALESCE(SUM(t.receipt_total), 0) "
                "FROM transactions t "
                "JOIN vendors v ON t.vendor_id = v.id "
                "WHERE v.name = ? AND t.market_day_id = 1 "
                "  AND t.status IN ('Confirmed', 'Adjusted')",
                (vendor_name,)).fetchone()[0]
            assert csv_total == db_total, (
                f"Vendor {vendor_name} reimbursement CSV={csv_total}c"
                f" != DB={db_total}c")

    def test_csv_total_matches_db_total(self, export_db, tmp_path):
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement,
        )
        from fam.utils.export import export_vendor_reimbursement
        rows = _collect_vendor_reimbursement(export_db, [1])
        out = str(tmp_path / 'vr.csv')
        export_vendor_reimbursement(rows, out)
        with open(out, encoding='utf-8') as f:
            csv_total = sum(round(float(r['Total Due to Vendor'])
                                  * 100)
                            for r in csv.DictReader(f))
        db_total = export_db.execute(
            "SELECT COALESCE(SUM(receipt_total), 0) "
            "FROM transactions WHERE market_day_id=1 "
            "  AND status IN ('Confirmed', 'Adjusted')"
        ).fetchone()[0]
        assert csv_total == db_total, (
            f"Vendor Reimbursement CSV grand total={csv_total}c "
            f"!= DB={db_total}c")

    def test_voided_excluded_from_export(self, export_db, tmp_path):
        """Voided transaction's $9.99 must not appear in the CSV."""
        from fam.sync.data_collector import (
            _collect_vendor_reimbursement,
        )
        from fam.utils.export import export_vendor_reimbursement
        rows = _collect_vendor_reimbursement(export_db, [1])
        out = str(tmp_path / 'vr.csv')
        export_vendor_reimbursement(rows, out)
        with open(out, encoding='utf-8') as f:
            content = f.read()
        # Voided txn was vendor V1, $9.99 → if it leaked, the V1
        # total would include the $9.99.  Compute V1 total and
        # confirm it equals the non-voided V1 receipt sum.
        with open(out, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                if r['Vendor'] == 'V1':
                    csv_v1_cents = round(
                        float(r['Total Due to Vendor']) * 100)
                    break
            else:
                pytest.fail("V1 row missing from CSV")
        db_v1_non_voided = export_db.execute(
            "SELECT COALESCE(SUM(receipt_total), 0) "
            "FROM transactions WHERE market_day_id=1 "
            "  AND vendor_id=1 AND status IN ('Confirmed', 'Adjusted')"
        ).fetchone()[0]
        assert csv_v1_cents == db_v1_non_voided


# ══════════════════════════════════════════════════════════════════
# FAM Match: CSV must agree with DB allocation/match totals
# ══════════════════════════════════════════════════════════════════

class TestFAMMatchExport:
    def test_csv_allocated_total_matches_db(self, export_db,
                                              tmp_path):
        from fam.sync.data_collector import _collect_fam_match
        from fam.utils.export import export_fam_match_report
        rows = _collect_fam_match(export_db, 1)
        out = str(tmp_path / 'fm.csv')
        export_fam_match_report(rows, out)
        with open(out, encoding='utf-8') as f:
            csv_alloc = sum(round(float(r['Total Allocated']) * 100)
                            for r in csv.DictReader(f))
        db_alloc = export_db.execute("""
            SELECT COALESCE(SUM(pli.method_amount), 0)
            FROM payment_line_items pli
            JOIN transactions t ON pli.transaction_id = t.id
            WHERE t.market_day_id=1
              AND t.status IN ('Confirmed', 'Adjusted')
        """).fetchone()[0]
        assert csv_alloc == db_alloc, (
            f"FAM Match CSV allocated total={csv_alloc}c != "
            f"DB={db_alloc}c")

    def test_csv_match_total_matches_db(self, export_db, tmp_path):
        from fam.sync.data_collector import _collect_fam_match
        from fam.utils.export import export_fam_match_report
        rows = _collect_fam_match(export_db, 1)
        out = str(tmp_path / 'fm.csv')
        export_fam_match_report(rows, out)
        with open(out, encoding='utf-8') as f:
            csv_match = sum(round(float(r['Total FAM Match']) * 100)
                            for r in csv.DictReader(f))
        db_match = export_db.execute("""
            SELECT COALESCE(SUM(pli.match_amount), 0)
            FROM payment_line_items pli
            JOIN transactions t ON pli.transaction_id = t.id
            WHERE t.market_day_id=1
              AND t.status IN ('Confirmed', 'Adjusted')
        """).fetchone()[0]
        assert csv_match == db_match, (
            f"FAM Match CSV match total={csv_match}c != "
            f"DB={db_match}c")


# ══════════════════════════════════════════════════════════════════
# Detailed Ledger: row-by-row receipt agreement
# ══════════════════════════════════════════════════════════════════

class TestDetailedLedgerExport:
    def test_per_row_receipt_matches_db(self, export_db, tmp_path):
        from fam.sync.data_collector import _collect_detailed_ledger
        from fam.utils.export import export_detailed_ledger
        rows = _collect_detailed_ledger(export_db, 1)
        out = str(tmp_path / 'dl.csv')
        export_detailed_ledger(rows, out)
        with open(out, encoding='utf-8') as f:
            csv_rows = list(csv.DictReader(f))
        non_voided_csv_total = sum(
            round(float(r.get('Receipt Total', '0') or 0) * 100)
            for r in csv_rows
            if r.get('Status') != 'Voided'
        )
        db_total = export_db.execute("""
            SELECT COALESCE(SUM(receipt_total), 0)
            FROM transactions WHERE market_day_id=1
              AND status IN ('Confirmed', 'Adjusted')
        """).fetchone()[0]
        assert non_voided_csv_total == db_total, (
            f"Detailed Ledger CSV non-voided total="
            f"{non_voided_csv_total}c != DB={db_total}c")


# ══════════════════════════════════════════════════════════════════
# Per-line invariant must hold in raw DB rows
# ══════════════════════════════════════════════════════════════════

class TestPerLineInvariant:
    """No payment_line_items row may violate
    customer_charged + match_amount = method_amount.
    This is the foundation of every report.
    """

    def test_no_rows_violate_invariant(self, export_db):
        n = export_db.execute("""
            SELECT COUNT(*) FROM payment_line_items
            WHERE customer_charged + match_amount != method_amount
        """).fetchone()[0]
        assert n == 0, (
            f"{n} payment_line_items violate "
            "customer + match = method_amount")

    def test_no_negative_amounts(self, export_db):
        n = export_db.execute("""
            SELECT COUNT(*) FROM payment_line_items
            WHERE method_amount < 0 OR match_amount < 0
              OR customer_charged < 0
        """).fetchone()[0]
        assert n == 0, f"{n} payment_line_items have negative amounts"
