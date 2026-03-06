"""Comprehensive tests for model CRUD operations.

Covers all public functions in:
  - market_day.py (market day lifecycle: create, open, close, reopen)
  - vendor.py (vendor CRUD, market assignments)
  - payment_method.py (payment method CRUD, market assignments)
  - customer_order.py (order lifecycle, voiding, label generation)
  - audit.py (log_action, get_audit_log, get_transaction_log)
  - transaction.py (search, draft queries, void, generate_transaction_id)
"""

import pytest
from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.models.market_day import (
    get_all_markets, get_all_market_days, get_market_day_by_id,
    get_open_market_day, find_market_day, create_market_day,
    close_market_day, reopen_market_day, get_market_day_transactions_summary,
)
from fam.models.vendor import (
    get_all_vendors, get_vendor_by_id, create_vendor,
    get_vendors_for_market, get_market_vendor_ids, get_vendor_market_ids,
    assign_vendor_to_market, unassign_vendor_from_market, update_vendor,
)
from fam.models.payment_method import (
    get_all_payment_methods, get_payment_method_by_id, create_payment_method,
    get_market_payment_method_ids, get_payment_methods_for_market,
    assign_payment_method_to_market, unassign_payment_method_from_market,
    update_payment_method,
)
from fam.models.customer_order import (
    generate_customer_label, create_customer_order, get_customer_order,
    get_order_transactions, get_order_total, get_order_vendor_summary,
    update_customer_order_status, update_customer_order_zip_code,
    void_customer_order, get_draft_orders_for_market_day,
)
from fam.models.transaction import (
    generate_transaction_id, create_transaction, get_transaction_by_id,
    get_transaction_by_fam_id, confirm_transaction, void_transaction,
    get_draft_transactions, search_transactions,
    save_payment_line_items, get_payment_line_items,
)
from fam.models.audit import log_action, get_audit_log, get_transaction_log


# ──────────────────────────────────────────────────────────────────
# Fixture: fresh database per test
# ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_models.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    yield conn
    close_connection()


def _seed_market(conn):
    """Seed a single market."""
    conn.execute(
        "INSERT INTO markets (id, name, address, daily_match_limit, match_limit_active)"
        " VALUES (1, 'Downtown Market', '123 Main St', 100.00, 1)"
    )
    conn.commit()


def _seed_full(conn):
    """Seed market + vendors + payment methods + market day."""
    _seed_market(conn)
    conn.execute("INSERT INTO vendors (id, name, contact_info) VALUES (1, 'Farm Stand', 'farm@test.com')")
    conn.execute("INSERT INTO vendors (id, name, contact_info) VALUES (2, 'Bakery', 'bakery@test.com')")
    conn.execute("INSERT INTO vendors (id, name, is_active) VALUES (3, 'Inactive Vendor', 0)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (1, 'SNAP', 100.0, 1, 1)"
    )
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (2, 'Cash', 0.0, 1, 2)"
    )
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, opened_by)"
        " VALUES (1, 1, '2026-03-01', 'Open', 'Alice')"
    )
    conn.execute(
        "INSERT INTO customer_orders (id, market_day_id, customer_label, zip_code)"
        " VALUES (1, 1, 'C-001', '12345')"
    )
    conn.commit()


# ══════════════════════════════════════════════════════════════════
# Market Day Model
# ══════════════════════════════════════════════════════════════════
class TestMarketDayCRUD:

    def test_get_all_markets(self, fresh_db):
        _seed_market(fresh_db)
        markets = get_all_markets()
        assert len(markets) == 1
        assert markets[0]['name'] == 'Downtown Market'

    def test_get_all_markets_empty(self, fresh_db):
        assert get_all_markets() == []

    def test_get_all_markets_sorted_by_name(self, fresh_db):
        fresh_db.execute("INSERT INTO markets (name) VALUES ('Zebra Market')")
        fresh_db.execute("INSERT INTO markets (name) VALUES ('Apple Market')")
        fresh_db.commit()
        markets = get_all_markets()
        assert markets[0]['name'] == 'Apple Market'
        assert markets[1]['name'] == 'Zebra Market'

    def test_create_market_day(self, fresh_db):
        _seed_market(fresh_db)
        md_id = create_market_day(1, '2026-03-15', opened_by='Bob')
        assert md_id > 0
        md = get_market_day_by_id(md_id)
        assert md['status'] == 'Open'
        assert md['opened_by'] == 'Bob'
        assert md['date'] == '2026-03-15'
        assert md['market_name'] == 'Downtown Market'

    def test_create_market_day_logs_audit(self, fresh_db):
        _seed_market(fresh_db)
        md_id = create_market_day(1, '2026-03-15', opened_by='Bob')
        logs = get_audit_log(table_name='market_days', record_id=md_id)
        assert len(logs) == 1
        assert logs[0]['action'] == 'OPEN'
        assert logs[0]['changed_by'] == 'Bob'

    def test_get_open_market_day(self, fresh_db):
        _seed_market(fresh_db)
        create_market_day(1, '2026-03-15', opened_by='Alice')
        md = get_open_market_day()
        assert md is not None
        assert md['status'] == 'Open'

    def test_get_open_market_day_none(self, fresh_db):
        _seed_market(fresh_db)
        assert get_open_market_day() is None

    def test_close_market_day(self, fresh_db):
        _seed_market(fresh_db)
        md_id = create_market_day(1, '2026-03-15')
        close_market_day(md_id, closed_by='Carol')
        md = get_market_day_by_id(md_id)
        assert md['status'] == 'Closed'
        assert md['closed_by'] == 'Carol'
        assert md['closed_at'] is not None
        assert get_open_market_day() is None

    def test_close_market_day_logs_audit(self, fresh_db):
        _seed_market(fresh_db)
        md_id = create_market_day(1, '2026-03-15')
        close_market_day(md_id, closed_by='Carol')
        logs = get_audit_log(table_name='market_days', record_id=md_id)
        actions = [l['action'] for l in logs]
        assert 'CLOSE' in actions

    def test_reopen_market_day(self, fresh_db):
        _seed_market(fresh_db)
        md_id = create_market_day(1, '2026-03-15')
        close_market_day(md_id)
        reopen_market_day(md_id, opened_by='Dave')
        md = get_market_day_by_id(md_id)
        assert md['status'] == 'Open'
        assert md['opened_by'] == 'Dave'

    def test_reopen_without_name(self, fresh_db):
        _seed_market(fresh_db)
        md_id = create_market_day(1, '2026-03-15', opened_by='Alice')
        close_market_day(md_id)
        reopen_market_day(md_id)  # no opened_by
        md = get_market_day_by_id(md_id)
        assert md['status'] == 'Open'

    def test_reopen_logs_audit(self, fresh_db):
        _seed_market(fresh_db)
        md_id = create_market_day(1, '2026-03-15')
        close_market_day(md_id)
        reopen_market_day(md_id, opened_by='Dave')
        logs = get_audit_log(table_name='market_days', record_id=md_id)
        actions = [l['action'] for l in logs]
        assert 'REOPEN' in actions

    def test_find_market_day(self, fresh_db):
        _seed_market(fresh_db)
        md_id = create_market_day(1, '2026-03-15')
        found = find_market_day(1, '2026-03-15')
        assert found is not None
        assert found['id'] == md_id

    def test_find_market_day_not_found(self, fresh_db):
        _seed_market(fresh_db)
        assert find_market_day(1, '2026-03-15') is None

    def test_find_market_day_wrong_market(self, fresh_db):
        _seed_market(fresh_db)
        create_market_day(1, '2026-03-15')
        assert find_market_day(999, '2026-03-15') is None

    def test_get_all_market_days(self, fresh_db):
        _seed_market(fresh_db)
        create_market_day(1, '2026-03-01')
        create_market_day(1, '2026-03-02')
        close_market_day(1)  # close first one
        days = get_all_market_days()
        assert len(days) == 2
        # Sorted by date DESC
        assert days[0]['date'] == '2026-03-02'

    def test_get_market_day_by_id_not_found(self, fresh_db):
        assert get_market_day_by_id(999) is None

    def test_get_market_day_transactions_summary(self, fresh_db):
        _seed_full(fresh_db)
        txn_id = create_transaction(1, 1, 50.00, 'FAM-20260301-0001')
        result = get_market_day_transactions_summary(1)
        assert len(result) == 1
        assert result[0]['receipt_total'] == 50.00
        assert result[0]['vendor_name'] == 'Farm Stand'

    def test_transactions_summary_empty(self, fresh_db):
        _seed_full(fresh_db)
        result = get_market_day_transactions_summary(1)
        assert result == []


# ══════════════════════════════════════════════════════════════════
# Vendor Model
# ══════════════════════════════════════════════════════════════════
class TestVendorCRUD:

    def test_create_vendor(self, fresh_db):
        vid = create_vendor('Test Farm', contact_info='test@farm.com')
        assert vid > 0
        v = get_vendor_by_id(vid)
        assert v['name'] == 'Test Farm'
        assert v['contact_info'] == 'test@farm.com'

    def test_create_vendor_minimal(self, fresh_db):
        vid = create_vendor('Minimal Farm')
        v = get_vendor_by_id(vid)
        assert v['name'] == 'Minimal Farm'
        assert v['contact_info'] is None

    def test_get_vendor_by_id_not_found(self, fresh_db):
        assert get_vendor_by_id(999) is None

    def test_get_all_vendors_unfiltered(self, fresh_db):
        _seed_full(fresh_db)
        vendors = get_all_vendors(active_only=False)
        assert len(vendors) == 3  # Farm Stand, Bakery, Inactive Vendor

    def test_get_all_vendors_active_only(self, fresh_db):
        _seed_full(fresh_db)
        vendors = get_all_vendors(active_only=True)
        assert len(vendors) == 2
        names = {v['name'] for v in vendors}
        assert 'Inactive Vendor' not in names

    def test_get_all_vendors_sorted_by_name(self, fresh_db):
        _seed_full(fresh_db)
        vendors = get_all_vendors()
        names = [v['name'] for v in vendors]
        assert names == sorted(names)

    def test_update_vendor_name(self, fresh_db):
        vid = create_vendor('Old Name')
        update_vendor(vid, name='New Name')
        assert get_vendor_by_id(vid)['name'] == 'New Name'

    def test_update_vendor_contact(self, fresh_db):
        vid = create_vendor('Farm')
        update_vendor(vid, contact_info='new@farm.com')
        assert get_vendor_by_id(vid)['contact_info'] == 'new@farm.com'

    def test_update_vendor_active_status(self, fresh_db):
        vid = create_vendor('Farm')
        update_vendor(vid, is_active=False)
        assert get_vendor_by_id(vid)['is_active'] == 0

    def test_update_vendor_no_fields(self, fresh_db):
        vid = create_vendor('Farm')
        update_vendor(vid)  # no fields — should do nothing
        assert get_vendor_by_id(vid)['name'] == 'Farm'


class TestVendorAssignments:

    def test_assign_vendor_to_market(self, fresh_db):
        _seed_full(fresh_db)
        assign_vendor_to_market(1, 1)
        ids = get_market_vendor_ids(1)
        assert 1 in ids

    def test_assign_idempotent(self, fresh_db):
        _seed_full(fresh_db)
        assign_vendor_to_market(1, 1)
        assign_vendor_to_market(1, 1)  # duplicate — should not error
        ids = get_market_vendor_ids(1)
        assert len(ids) == 1

    def test_unassign_vendor(self, fresh_db):
        _seed_full(fresh_db)
        assign_vendor_to_market(1, 1)
        unassign_vendor_from_market(1, 1)
        assert get_market_vendor_ids(1) == set()

    def test_unassign_nonexistent(self, fresh_db):
        _seed_full(fresh_db)
        unassign_vendor_from_market(1, 999)  # no-op, should not error

    def test_get_vendors_for_market(self, fresh_db):
        _seed_full(fresh_db)
        assign_vendor_to_market(1, 1)
        assign_vendor_to_market(1, 2)
        vendors = get_vendors_for_market(1)
        assert len(vendors) == 2
        names = {v['name'] for v in vendors}
        assert 'Farm Stand' in names
        assert 'Bakery' in names

    def test_get_vendors_for_market_active_only(self, fresh_db):
        _seed_full(fresh_db)
        assign_vendor_to_market(1, 1)  # active
        assign_vendor_to_market(1, 3)  # inactive
        active = get_vendors_for_market(1, active_only=True)
        all_v = get_vendors_for_market(1, active_only=False)
        assert len(active) == 1
        assert len(all_v) == 2

    def test_get_vendor_market_ids(self, fresh_db):
        _seed_full(fresh_db)
        fresh_db.execute("INSERT INTO markets (id, name) VALUES (2, 'Riverside Market')")
        fresh_db.commit()
        assign_vendor_to_market(1, 1)
        assign_vendor_to_market(2, 1)
        market_ids = get_vendor_market_ids(1)
        assert market_ids == {1, 2}

    def test_get_market_vendor_ids_empty(self, fresh_db):
        _seed_full(fresh_db)
        assert get_market_vendor_ids(1) == set()


# ══════════════════════════════════════════════════════════════════
# Payment Method Model
# ══════════════════════════════════════════════════════════════════
class TestPaymentMethodCRUD:

    def test_create_payment_method(self, fresh_db):
        pid = create_payment_method('SNAP', 100.0, sort_order=1)
        assert pid > 0
        pm = get_payment_method_by_id(pid)
        assert pm['name'] == 'SNAP'
        assert pm['match_percent'] == 100.0
        assert pm['sort_order'] == 1

    def test_create_zero_match(self, fresh_db):
        pid = create_payment_method('Cash', 0.0)
        assert get_payment_method_by_id(pid)['match_percent'] == 0.0

    def test_get_by_id_not_found(self, fresh_db):
        assert get_payment_method_by_id(999) is None

    def test_get_all_payment_methods(self, fresh_db):
        _seed_full(fresh_db)
        methods = get_all_payment_methods()
        assert len(methods) == 2
        assert methods[0]['sort_order'] <= methods[1]['sort_order']

    def test_get_all_active_only(self, fresh_db):
        _seed_full(fresh_db)
        fresh_db.execute(
            "INSERT INTO payment_methods (name, match_percent, is_active, sort_order)"
            " VALUES ('Disabled', 50.0, 0, 99)"
        )
        fresh_db.commit()
        all_m = get_all_payment_methods(active_only=False)
        active = get_all_payment_methods(active_only=True)
        assert len(all_m) == 3
        assert len(active) == 2

    def test_update_payment_method_name(self, fresh_db):
        pid = create_payment_method('Old', 50.0)
        update_payment_method(pid, name='New')
        assert get_payment_method_by_id(pid)['name'] == 'New'

    def test_update_match_percent(self, fresh_db):
        pid = create_payment_method('Test', 50.0)
        update_payment_method(pid, match_percent=200.0)
        assert get_payment_method_by_id(pid)['match_percent'] == 200.0

    def test_update_sort_order(self, fresh_db):
        pid = create_payment_method('Test', 50.0, sort_order=1)
        update_payment_method(pid, sort_order=5)
        assert get_payment_method_by_id(pid)['sort_order'] == 5

    def test_update_no_fields(self, fresh_db):
        pid = create_payment_method('Test', 50.0)
        update_payment_method(pid)  # no-op
        assert get_payment_method_by_id(pid)['name'] == 'Test'

    def test_update_deactivate(self, fresh_db):
        pid = create_payment_method('Test', 50.0)
        update_payment_method(pid, is_active=False)
        assert get_payment_method_by_id(pid)['is_active'] == 0


class TestPaymentMethodAssignments:

    def test_assign_to_market(self, fresh_db):
        _seed_full(fresh_db)
        assign_payment_method_to_market(1, 1)
        ids = get_market_payment_method_ids(1)
        assert 1 in ids

    def test_assign_idempotent(self, fresh_db):
        _seed_full(fresh_db)
        assign_payment_method_to_market(1, 1)
        assign_payment_method_to_market(1, 1)
        assert len(get_market_payment_method_ids(1)) == 1

    def test_unassign(self, fresh_db):
        _seed_full(fresh_db)
        assign_payment_method_to_market(1, 1)
        unassign_payment_method_from_market(1, 1)
        assert get_market_payment_method_ids(1) == set()

    def test_get_for_market(self, fresh_db):
        _seed_full(fresh_db)
        assign_payment_method_to_market(1, 1)
        assign_payment_method_to_market(1, 2)
        methods = get_payment_methods_for_market(1)
        assert len(methods) == 2

    def test_get_for_market_active_only(self, fresh_db):
        _seed_full(fresh_db)
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
            " VALUES (3, 'Inactive PM', 50.0, 0, 99)"
        )
        fresh_db.commit()
        assign_payment_method_to_market(1, 1)
        assign_payment_method_to_market(1, 3)
        active = get_payment_methods_for_market(1, active_only=True)
        all_m = get_payment_methods_for_market(1, active_only=False)
        assert len(active) == 1
        assert len(all_m) == 2


# ══════════════════════════════════════════════════════════════════
# Customer Order Model
# ══════════════════════════════════════════════════════════════════
class TestCustomerOrderCRUD:

    def test_generate_label_first(self, fresh_db):
        _seed_full(fresh_db)
        # C-001 already exists from seed, so next should be C-002
        label = generate_customer_label(1)
        assert label == 'C-002'

    def test_generate_label_empty_market_day(self, fresh_db):
        _seed_market(fresh_db)
        fresh_db.execute(
            "INSERT INTO market_days (id, market_id, date, status) VALUES (1, 1, '2026-03-01', 'Open')"
        )
        fresh_db.commit()
        label = generate_customer_label(1)
        assert label == 'C-001'

    def test_create_customer_order(self, fresh_db):
        _seed_full(fresh_db)
        oid, label = create_customer_order(1)
        assert oid > 0
        assert label == 'C-002'

    def test_create_with_custom_label(self, fresh_db):
        _seed_full(fresh_db)
        oid, label = create_customer_order(1, customer_label='C-001')
        assert label == 'C-001'

    def test_create_with_zip(self, fresh_db):
        _seed_full(fresh_db)
        oid, label = create_customer_order(1, zip_code='99999')
        order = get_customer_order(oid)
        assert order['zip_code'] == '99999'

    def test_get_customer_order(self, fresh_db):
        _seed_full(fresh_db)
        order = get_customer_order(1)
        assert order is not None
        assert order['customer_label'] == 'C-001'
        assert order['market_name'] == 'Downtown Market'
        assert order['daily_match_limit'] == 100.00

    def test_get_customer_order_not_found(self, fresh_db):
        assert get_customer_order(999) is None

    def test_update_status(self, fresh_db):
        _seed_full(fresh_db)
        update_customer_order_status(1, 'Confirmed')
        order = get_customer_order(1)
        assert order['status'] == 'Confirmed'

    def test_update_zip(self, fresh_db):
        _seed_full(fresh_db)
        update_customer_order_zip_code(1, '54321')
        order = get_customer_order(1)
        assert order['zip_code'] == '54321'

    def test_update_zip_to_none(self, fresh_db):
        _seed_full(fresh_db)
        update_customer_order_zip_code(1, None)
        order = get_customer_order(1)
        assert order['zip_code'] is None


class TestCustomerOrderTransactions:

    def _create_txn(self, fresh_db, receipt=50.0, status='Confirmed'):
        txn_id, _fam_id = create_transaction(1, 1, receipt,
                                             customer_order_id=1)
        if status == 'Confirmed':
            confirm_transaction(txn_id, confirmed_by='Alice')
        return txn_id

    def test_get_order_transactions(self, fresh_db):
        _seed_full(fresh_db)
        self._create_txn(fresh_db, 50.0)
        self._create_txn(fresh_db, 30.0)
        txns = get_order_transactions(1)
        assert len(txns) == 2

    def test_get_order_transactions_excludes_voided(self, fresh_db):
        _seed_full(fresh_db)
        tid = self._create_txn(fresh_db, 50.0)
        self._create_txn(fresh_db, 30.0)
        void_transaction(tid)
        txns = get_order_transactions(1)
        assert len(txns) == 1
        assert txns[0]['receipt_total'] == 30.0

    def test_get_order_total(self, fresh_db):
        _seed_full(fresh_db)
        self._create_txn(fresh_db, 50.0)
        self._create_txn(fresh_db, 30.0)
        assert get_order_total(1) == 80.0

    def test_get_order_total_excludes_voided(self, fresh_db):
        _seed_full(fresh_db)
        tid = self._create_txn(fresh_db, 50.0)
        self._create_txn(fresh_db, 30.0)
        void_transaction(tid)
        assert get_order_total(1) == 30.0

    def test_get_order_total_empty(self, fresh_db):
        _seed_full(fresh_db)
        assert get_order_total(1) == 0.0

    def test_get_order_vendor_summary(self, fresh_db):
        _seed_full(fresh_db)
        self._create_txn(fresh_db, 50.0)
        tid2, _fam2 = create_transaction(1, 2, 30.0, customer_order_id=1)
        confirm_transaction(tid2, confirmed_by='Alice')
        summary = get_order_vendor_summary(1)
        assert len(summary) == 2
        totals = {s['vendor_name']: s['vendor_total'] for s in summary}
        assert totals['Farm Stand'] == 50.0
        assert totals['Bakery'] == 30.0

    def test_void_customer_order(self, fresh_db):
        _seed_full(fresh_db)
        self._create_txn(fresh_db, 50.0)
        self._create_txn(fresh_db, 30.0)
        void_customer_order(1)
        order = get_customer_order(1)
        assert order['status'] == 'Voided'
        # All transactions voided
        txns = get_order_transactions(1)
        assert txns == []  # voided are excluded
        assert get_order_total(1) == 0.0

    def test_void_customer_order_audit_logged(self, fresh_db):
        _seed_full(fresh_db)
        self._create_txn(fresh_db, 50.0)
        void_customer_order(1)
        logs = get_audit_log(table_name='customer_orders', record_id=1)
        actions = [l['action'] for l in logs]
        assert 'VOID' in actions

    def test_get_draft_orders(self, fresh_db):
        _seed_full(fresh_db)
        # Order 1 is Draft by default
        create_transaction(1, 1, 50.0, customer_order_id=1)
        drafts = get_draft_orders_for_market_day(1)
        assert len(drafts) == 1
        assert drafts[0]['customer_label'] == 'C-001'
        assert drafts[0]['order_total'] == 50.0

    def test_get_draft_orders_excludes_confirmed(self, fresh_db):
        _seed_full(fresh_db)
        update_customer_order_status(1, 'Confirmed')
        drafts = get_draft_orders_for_market_day(1)
        assert len(drafts) == 0


# ══════════════════════════════════════════════════════════════════
# Transaction Model (search, drafts, void, ID generation)
# ══════════════════════════════════════════════════════════════════
class TestTransactionExtended:

    def test_generate_transaction_id_first(self, fresh_db):
        _seed_full(fresh_db)
        fam_id = generate_transaction_id('2026-03-01')
        assert fam_id.startswith('FAM-')
        assert fam_id.endswith('-0001')

    def test_generate_transaction_id_sequential(self, fresh_db):
        _seed_full(fresh_db)
        create_transaction(1, 1, 50.0)
        fam_id = generate_transaction_id('2026-03-01')
        assert fam_id.endswith('-0002')

    def test_void_transaction(self, fresh_db):
        _seed_full(fresh_db)
        tid, _fam = create_transaction(1, 1, 50.0)
        void_transaction(tid)
        txn = get_transaction_by_id(tid)
        assert txn['status'] == 'Voided'

    def test_get_draft_transactions(self, fresh_db):
        _seed_full(fresh_db)
        create_transaction(1, 1, 50.0)
        create_transaction(1, 1, 30.0)
        drafts = get_draft_transactions(1)
        assert len(drafts) == 2
        assert all(d['status'] == 'Draft' for d in drafts)

    def test_get_draft_transactions_excludes_confirmed(self, fresh_db):
        _seed_full(fresh_db)
        tid1, _ = create_transaction(1, 1, 50.0)
        create_transaction(1, 1, 30.0)
        confirm_transaction(tid1, confirmed_by='Alice')
        drafts = get_draft_transactions(1)
        assert len(drafts) == 1
        assert drafts[0]['receipt_total'] == 30.0

    def test_search_transactions_no_filter(self, fresh_db):
        _seed_full(fresh_db)
        create_transaction(1, 1, 50.0, customer_order_id=1)
        results = search_transactions()
        assert len(results) == 1

    def test_search_by_market_day(self, fresh_db):
        _seed_full(fresh_db)
        create_transaction(1, 1, 50.0)
        results = search_transactions(market_day_id=1)
        assert len(results) == 1
        results = search_transactions(market_day_id=999)
        assert len(results) == 0

    def test_search_by_vendor(self, fresh_db):
        _seed_full(fresh_db)
        create_transaction(1, 1, 50.0)
        create_transaction(1, 2, 30.0)
        results = search_transactions(vendor_id=1)
        assert len(results) == 1
        assert results[0]['vendor_name'] == 'Farm Stand'

    def test_search_by_status(self, fresh_db):
        _seed_full(fresh_db)
        tid, _ = create_transaction(1, 1, 50.0)
        create_transaction(1, 1, 30.0)
        confirm_transaction(tid, confirmed_by='Alice')
        results = search_transactions(status='Confirmed')
        assert len(results) == 1
        assert results[0]['receipt_total'] == 50.0

    def test_search_by_fam_id(self, fresh_db):
        _seed_full(fresh_db)
        _, fam1 = create_transaction(1, 1, 50.0)
        _, fam2 = create_transaction(1, 1, 30.0)
        # Search by the last segment of the second FAM ID
        search_term = fam2.split('-')[-1]
        results = search_transactions(fam_id_search=search_term)
        assert len(results) == 1
        assert results[0]['fam_transaction_id'] == fam2

    def test_search_combined_filters(self, fresh_db):
        _seed_full(fresh_db)
        create_transaction(1, 1, 50.0)
        create_transaction(1, 2, 30.0)
        results = search_transactions(market_day_id=1, vendor_id=2)
        assert len(results) == 1
        assert results[0]['vendor_name'] == 'Bakery'

    def test_get_transaction_by_fam_id(self, fresh_db):
        _seed_full(fresh_db)
        _, fam_id = create_transaction(1, 1, 50.0)
        txn = get_transaction_by_fam_id(fam_id)
        assert txn is not None
        assert txn['receipt_total'] == 50.0

    def test_get_transaction_by_fam_id_not_found(self, fresh_db):
        assert get_transaction_by_fam_id('FAM-NOPE-0000') is None


# ══════════════════════════════════════════════════════════════════
# Audit Log Model
# ══════════════════════════════════════════════════════════════════
class TestAuditLog:

    def test_log_action_basic(self, fresh_db):
        log_action('transactions', 1, 'CREATE', 'Alice', notes='Test')
        logs = get_audit_log(table_name='transactions', record_id=1)
        assert len(logs) == 1
        assert logs[0]['action'] == 'CREATE'
        assert logs[0]['changed_by'] == 'Alice'
        assert logs[0]['notes'] == 'Test'

    def test_log_action_with_field_change(self, fresh_db):
        log_action('transactions', 1, 'ADJUST', 'Bob',
                   field_name='receipt_total', old_value=50.0, new_value=75.0)
        logs = get_audit_log(record_id=1)
        assert logs[0]['field_name'] == 'receipt_total'
        assert logs[0]['old_value'] == '50.0'
        assert logs[0]['new_value'] == '75.0'

    def test_log_action_old_value_none(self, fresh_db):
        log_action('transactions', 1, 'CREATE', 'Alice',
                   old_value=None, new_value='Confirmed')
        logs = get_audit_log(record_id=1)
        assert logs[0]['old_value'] is None

    def test_get_audit_log_filter_table(self, fresh_db):
        log_action('transactions', 1, 'CREATE', 'Alice')
        log_action('market_days', 1, 'OPEN', 'Bob')
        txn_logs = get_audit_log(table_name='transactions')
        assert len(txn_logs) == 1
        assert txn_logs[0]['table_name'] == 'transactions'

    def test_get_audit_log_limit(self, fresh_db):
        for i in range(10):
            log_action('transactions', i, 'CREATE', 'Alice')
        logs = get_audit_log(limit=5)
        assert len(logs) == 5

    def test_get_audit_log_empty(self, fresh_db):
        assert get_audit_log() == []

    def test_get_transaction_log(self, fresh_db):
        _seed_full(fresh_db)
        tid, _fam = create_transaction(1, 1, 50.0)
        log_action('transactions', tid, 'CREATE', 'Alice',
                   notes='Transaction created')
        logs = get_transaction_log(market_day_id=1)
        assert len(logs) >= 1
        # Should have FAM transaction ID enrichment
        txn_logs = [l for l in logs if l.get('fam_transaction_id')]
        assert len(txn_logs) >= 1

    def test_get_transaction_log_action_filter(self, fresh_db):
        _seed_full(fresh_db)
        tid, _fam = create_transaction(1, 1, 50.0)
        log_action('transactions', tid, 'CREATE', 'Alice')
        log_action('transactions', tid, 'CONFIRM', 'Alice')
        logs = get_transaction_log(action_filter=['CREATE'])
        create_logs = [l for l in logs if l['action'] == 'CREATE']
        confirm_logs = [l for l in logs if l['action'] == 'CONFIRM']
        assert len(create_logs) >= 1
        assert len(confirm_logs) == 0

    def test_get_transaction_log_empty(self, fresh_db):
        logs = get_transaction_log()
        assert logs == []


# ══════════════════════════════════════════════════════════════════
# Data Integrity: FK constraints, status transitions
# ══════════════════════════════════════════════════════════════════
class TestDataIntegrity:

    def test_transaction_requires_valid_vendor(self, fresh_db):
        _seed_full(fresh_db)
        with pytest.raises(Exception):
            create_transaction(1, 999, 50.0)

    def test_transaction_requires_valid_market_day(self, fresh_db):
        _seed_full(fresh_db)
        with pytest.raises(Exception):
            create_transaction(999, 1, 50.0)

    def test_market_day_requires_valid_market(self, fresh_db):
        with pytest.raises(Exception):
            create_market_day(999, '2026-03-15')

    def test_confirm_sets_status_and_timestamp(self, fresh_db):
        _seed_full(fresh_db)
        tid, _fam = create_transaction(1, 1, 50.0)
        confirm_transaction(tid, confirmed_by='Alice')
        txn = get_transaction_by_id(tid)
        assert txn['status'] == 'Confirmed'
        assert txn['confirmed_by'] == 'Alice'
        assert txn['confirmed_at'] is not None

    def test_payment_line_items_round_trip(self, fresh_db):
        _seed_full(fresh_db)
        tid, _fam = create_transaction(1, 1, 100.0)
        items = [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 100.0,
            'match_amount': 50.0,
            'customer_charged': 50.0,
        }]
        save_payment_line_items(tid, items)
        retrieved = get_payment_line_items(tid)
        assert len(retrieved) == 1
        assert retrieved[0]['method_amount'] == 100.0
        assert retrieved[0]['match_amount'] == 50.0

    def test_save_payment_items_replaces(self, fresh_db):
        _seed_full(fresh_db)
        tid, _fam = create_transaction(1, 1, 100.0)
        items1 = [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 100.0,
            'match_amount': 50.0,
            'customer_charged': 50.0,
        }]
        save_payment_line_items(tid, items1)
        items2 = [{
            'payment_method_id': 2,
            'method_name_snapshot': 'Cash',
            'match_percent_snapshot': 0.0,
            'method_amount': 100.0,
            'match_amount': 0.0,
            'customer_charged': 100.0,
        }]
        save_payment_line_items(tid, items2)
        retrieved = get_payment_line_items(tid)
        assert len(retrieved) == 1
        assert retrieved[0]['method_name_snapshot'] == 'Cash'

    def test_fmnp_entry_soft_delete_preserves_row(self, fresh_db):
        """Verify FMNP soft-delete keeps the row with status='Deleted'."""
        _seed_full(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, delete_fmnp_entry, get_fmnp_entries
        eid = create_fmnp_entry(1, 1, 40.0, 'Admin')
        delete_fmnp_entry(eid)
        # Row still exists
        row = fresh_db.execute(
            "SELECT status FROM fmnp_entries WHERE id=?", (eid,)
        ).fetchone()
        assert row['status'] == 'Deleted'
        # But filtered out by default
        entries = get_fmnp_entries(market_day_id=1)
        assert all(e['id'] != eid for e in entries)

    def test_fmnp_active_only_false_returns_deleted(self, fresh_db):
        _seed_full(fresh_db)
        from fam.models.fmnp import create_fmnp_entry, delete_fmnp_entry, get_fmnp_entries
        eid = create_fmnp_entry(1, 1, 40.0, 'Admin')
        delete_fmnp_entry(eid)
        entries = get_fmnp_entries(market_day_id=1, active_only=False)
        statuses = [e['status'] for e in entries]
        assert 'Deleted' in statuses
