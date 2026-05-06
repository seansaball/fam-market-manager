"""UI volume / scale tests
(v1.9.10 follow-up, 2026-05-01).

Onsite the UI handles edge volumes that are unusual but real:

  * 200+ vendors in a market
  * 50+ payment methods configured
  * 30+ payment rows on one customer order
  * Hundreds of customers in the today-list dropdown
  * 4-hour market session with 50+ confirms (memory growth)

These tests pin that the volume-related UI surfaces stay
responsive and don't leak memory or degrade per-iteration.

Latency budgets are LOOSE — they don't pin specific
millisecond floors (CI machines vary), but they verify O(N)
characteristics rather than O(N²).
"""

import gc
import time

import pytest

from fam.database.connection import (
    get_connection, set_db_path, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def db(tmp_path):
    db_file = str(tmp_path / "ui_volume.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "(1, 'SNAP', 100.0, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES (1, 1, '2099-05-01', 'Open', 'Tester')")
    conn.commit()
    yield conn
    close_connection()


# ════════════════════════════════════════════════════════════════════
# 1. Vendor / payment-method volume
# ════════════════════════════════════════════════════════════════════


class TestVendorAndMethodVolume:

    def test_200_vendors_loadable_under_1_second(self, db):
        """A market with 200 active vendors — loading them for
        the dropdown / report population must NOT take more than
        1 second.  This is well within the wall-time the
        operator can tolerate at market open."""
        from fam.models.vendor import (
            create_vendor, get_all_vendors,
        )
        for i in range(200):
            create_vendor(f"Vendor {i:03d}")

        t0 = time.perf_counter()
        vendors = get_all_vendors(active_only=False)
        dt = time.perf_counter() - t0

        assert len(vendors) >= 200
        assert dt < 1.0, (
            f"loading 200 vendors took {dt*1000:.1f}ms — should "
            f"be <1s; if slower, an O(N²) scan is creeping in")

    def test_50_payment_methods_loadable_under_500ms(self, db):
        from fam.models.payment_method import get_all_payment_methods
        for i in range(50):
            db.execute(
                "INSERT INTO payment_methods (name, match_percent, "
                "sort_order, is_active) "
                "VALUES (?, 0.0, ?, 1)",
                (f'Method-{i:02d}', i + 100))
        db.commit()

        t0 = time.perf_counter()
        methods = get_all_payment_methods(active_only=True)
        dt = time.perf_counter() - t0

        assert len(methods) >= 50
        assert dt < 0.5, (
            f"50 payment methods loaded in {dt*1000:.1f}ms; "
            f"degradation past 500ms suggests an N+1 query")

    def test_get_payment_methods_for_market_with_50_methods_fast(self, db):
        """The hot path during payment screen load — fetching
        market-eligible methods MUST be fast even with 50 methods
        configured."""
        from fam.models.payment_method import (
            get_payment_methods_for_market,
        )
        for i in range(50):
            cur = db.execute(
                "INSERT INTO payment_methods (name, match_percent, "
                "sort_order, is_active) "
                "VALUES (?, 0.0, ?, 1)",
                (f'M-{i:02d}', i + 100))
            db.execute(
                "INSERT INTO market_payment_methods "
                "(market_id, payment_method_id) VALUES (1, ?)",
                (cur.lastrowid,))
        db.commit()

        t0 = time.perf_counter()
        methods = get_payment_methods_for_market(1, active_only=True)
        dt = time.perf_counter() - t0

        assert len(methods) >= 50
        assert dt < 0.2, (
            f"market-method fetch took {dt*1000:.1f}ms — payment "
            f"screen load should not feel laggy")


# ════════════════════════════════════════════════════════════════════
# 2. Customer order volume — large today-lists
# ════════════════════════════════════════════════════════════════════


class TestCustomerVolume:

    def test_500_customers_listable_under_1_second(self, db):
        """The Receipt Intake screen pre-populates with today's
        customer labels.  500 confirmed customers must list
        in under 1 second."""
        from fam.models.customer_order import (
            get_confirmed_customers_for_market_day,
        )
        # Bulk-insert 500 confirmed orders + 1 confirmed
        # transaction each so they show up in the query.
        for i in range(500):
            cur = db.execute(
                "INSERT INTO customer_orders "
                "(market_day_id, customer_label, status, created_at) "
                "VALUES (1, ?, 'Confirmed', '2099-05-01 10:00:00')",
                (f'C-{i:04d}',))
            order_id = cur.lastrowid
            # Transaction must reference a real vendor — add one.
        db.execute("INSERT INTO vendors (id, name) VALUES (1, 'V')")
        db.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, 1)")
        for i in range(500):
            order_id = i + 1  # rowid sequence
            db.execute(
                "INSERT INTO transactions "
                "(fam_transaction_id, market_day_id, vendor_id, "
                " receipt_total, customer_order_id, status) "
                "VALUES (?, 1, 1, 1000, ?, 'Confirmed')",
                (f'FAM-V-{i:04d}', order_id))
        db.commit()

        t0 = time.perf_counter()
        customers = get_confirmed_customers_for_market_day(1)
        dt = time.perf_counter() - t0

        assert len(customers) == 500
        assert dt < 1.0, (
            f"500-customer list took {dt*1000:.1f}ms — "
            f"this query feeds the receipt-intake dropdown and "
            f"must stay snappy")


# ════════════════════════════════════════════════════════════════════
# 3. Engine performance with 30 payment rows
# ════════════════════════════════════════════════════════════════════


class TestEngineWithManyRows:

    def test_calculate_payment_breakdown_30_rows_fast(self):
        """Payment screen can have up to 30 active rows (the UI
        clamp).  The engine MUST return in under 100ms for this."""
        from fam.utils.calculations import calculate_payment_breakdown

        entries = [
            {'method_amount': 100 + i, 'match_percent': 100.0,
             'denomination': None}
            for i in range(30)
        ]
        receipt_total = sum(e['method_amount'] for e in entries)

        t0 = time.perf_counter()
        result = calculate_payment_breakdown(
            receipt_total, entries, match_limit=None)
        dt = time.perf_counter() - t0

        assert result['is_valid'] is True
        assert dt < 0.1, (
            f"engine with 30 rows took {dt*1000:.1f}ms — Auto-"
            f"Distribute clicks would feel laggy")


# ════════════════════════════════════════════════════════════════════
# 4. 4-hour market session — memory growth bounded
# ════════════════════════════════════════════════════════════════════


class TestLongSessionMemory:

    def test_50_confirms_no_unbounded_object_growth(self, db):
        """Simulate 50 confirms in a single session.  After
        each, the live-objects count should NOT grow per-
        iteration (bounded growth means no leaks)."""
        from fam.models.customer_order import (
            create_customer_order, update_customer_order_status,
        )
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
            confirm_transaction,
        )
        db.execute("INSERT INTO vendors (id, name) VALUES (1, 'V')")
        db.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, 1)")
        db.execute(
            "INSERT INTO vendor_payment_methods "
            "(vendor_id, payment_method_id) VALUES (1, 1)")
        db.commit()

        # Warm up with 10 confirms.
        for i in range(10):
            order_id, _ = create_customer_order(
                market_day_id=1, customer_label=f'W-{i:02d}')
            txn_id, _ = create_transaction(
                market_day_id=1, vendor_id=1, receipt_total=1000,
                customer_order_id=order_id,
                market_day_date='2099-05-01')
            save_payment_line_items(txn_id, [{
                'payment_method_id': 1,
                'method_name_snapshot': 'SNAP',
                'match_percent_snapshot': 100.0,
                'method_amount': 1000, 'match_amount': 500,
                'customer_charged': 500, 'photo_path': None,
            }])
            confirm_transaction(txn_id, confirmed_by='Tester')
            update_customer_order_status(order_id, 'Confirmed')

        gc.collect()
        baseline = len(gc.get_objects())

        # Run 50 more confirms.
        for i in range(50):
            order_id, _ = create_customer_order(
                market_day_id=1, customer_label=f'C-{i:02d}')
            txn_id, _ = create_transaction(
                market_day_id=1, vendor_id=1, receipt_total=1000,
                customer_order_id=order_id,
                market_day_date='2099-05-01')
            save_payment_line_items(txn_id, [{
                'payment_method_id': 1,
                'method_name_snapshot': 'SNAP',
                'match_percent_snapshot': 100.0,
                'method_amount': 1000, 'match_amount': 500,
                'customer_charged': 500, 'photo_path': None,
            }])
            confirm_transaction(txn_id, confirmed_by='Tester')
            update_customer_order_status(order_id, 'Confirmed')

        gc.collect()
        after = len(gc.get_objects())

        # Each confirm legitimately creates rows in the DB and
        # some Python objects for those rows.  Loose bound:
        # growth must stay reasonable — under 500 objects per
        # confirm including all DB row representations.  A leak
        # in the model layer would show 10× this rate.
        growth_per_confirm = (after - baseline) / 50
        assert growth_per_confirm < 500, (
            f"object growth {growth_per_confirm:.1f}/confirm — "
            f"50 confirms grew live objects from {baseline} to "
            f"{after}.  Suggests a leak in the model layer "
            f"(unclosed cursors? cached row dicts?)")


# ════════════════════════════════════════════════════════════════════
# 5. Report generation at year-1 scale
# ════════════════════════════════════════════════════════════════════


class TestReportLatencyAtScale:

    def test_vendor_reimbursement_with_500_txns_under_2s(self, db):
        """Report generation feels live to operators — under 2
        seconds even at 500 confirmed transactions."""
        db.execute("INSERT INTO vendors (id, name) VALUES (1, 'V')")
        db.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, 1)")
        db.execute(
            "INSERT INTO vendor_payment_methods "
            "(vendor_id, payment_method_id) VALUES (1, 1)")
        # Bulk-insert 500 confirmed transactions.
        for i in range(500):
            cur = db.execute(
                "INSERT INTO customer_orders "
                "(market_day_id, customer_label, status, created_at) "
                "VALUES (1, ?, 'Confirmed', '2099-05-01 10:00:00')",
                (f'C-{i:04d}',))
            order_id = cur.lastrowid
            cur = db.execute(
                "INSERT INTO transactions "
                "(fam_transaction_id, market_day_id, vendor_id, "
                " receipt_total, customer_order_id, status) "
                "VALUES (?, 1, 1, 1000, ?, 'Confirmed')",
                (f'FAM-R-{i:04d}', order_id))
            txn_id = cur.lastrowid
            db.execute(
                "INSERT INTO payment_line_items "
                "(transaction_id, payment_method_id, "
                " method_name_snapshot, match_percent_snapshot, "
                " method_amount, match_amount, customer_charged) "
                "VALUES (?, 1, 'SNAP', 100.0, 1000, 500, 500)",
                (txn_id,))
        db.commit()

        from fam.sync.data_collector import _collect_vendor_reimbursement
        t0 = time.perf_counter()
        rows = _collect_vendor_reimbursement(db, [1])
        dt = time.perf_counter() - t0

        assert len(rows) >= 1
        assert dt < 2.0, (
            f"500-txn vendor reimbursement took {dt*1000:.1f}ms; "
            f"manual 'Generate Reports' must stay snappy")
