"""Audit-log coverage inventory.

These tests pin **what is** audit-logged today so future regressions
are obvious — anything that is logged stays logged.

History
-------
The v1.9.9 production audit found several CRUD paths that did NOT
call ``log_action()``: vendor create/update, payment-method
create/update, vendor↔market and vendor↔method eligibility changes.
v1.9.10 closed those gaps (commit accompanying this test file
update).

What remains intentionally un-logged: low-level app_settings
key-value writes (market_code / device_id / sync credentials /
update flags).  These do not affect transactions; tests in the
``TestRemainingNonFinancialGaps`` class document this.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def fresh_db(tmp_path):
    db = str(tmp_path / 'audit_gap.db')
    close_connection()
    set_db_path(db)
    initialize_database()
    conn = get_connection()
    # One market + market day so transaction-level operations have
    # somewhere to land.
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 50000, 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-29', 'Open', 'T')")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V1')")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active) VALUES (1, 'SNAP', 100.0, 1, 1)")
    conn.commit()
    yield conn
    close_connection()


# ══════════════════════════════════════════════════════════════════
# Logged surfaces — these must keep logging
# ══════════════════════════════════════════════════════════════════

class TestFinancialActionsAreAlwaysLogged:
    """Regression alarm: every financially-meaningful lifecycle
    action must produce an audit_log row.  If any of these fail,
    a refactor accidentally removed audit coverage."""

    def test_create_transaction_logs_create(self, fresh_db):
        from fam.models.transaction import create_transaction
        create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            market_day_date='2026-04-29')
        n = fresh_db.execute(
            "SELECT COUNT(*) FROM audit_log WHERE table_name="
            "'transactions' AND action='CREATE'").fetchone()[0]
        assert n == 1

    def test_confirm_transaction_logs_confirm(self, fresh_db):
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
            confirm_transaction,
        )
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            market_day_date='2026-04-29')
        save_payment_line_items(tid, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 1000, 'match_amount': 500,
            'customer_charged': 500,
        }])
        confirm_transaction(tid, confirmed_by='Tester')
        n = fresh_db.execute(
            "SELECT COUNT(*) FROM audit_log WHERE table_name="
            "'transactions' AND action='CONFIRM' AND record_id=?",
            (tid,)).fetchone()[0]
        assert n == 1

    def test_save_payment_logs_payment_saved(self, fresh_db):
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
        )
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            market_day_date='2026-04-29')
        save_payment_line_items(tid, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 1000, 'match_amount': 500,
            'customer_charged': 500,
        }])
        n = fresh_db.execute(
            "SELECT COUNT(*) FROM audit_log WHERE table_name="
            "'payment_line_items' AND action='PAYMENT_SAVED' "
            " AND record_id=?", (tid,)).fetchone()[0]
        assert n == 1

    def test_void_transaction_logs_void(self, fresh_db):
        from fam.models.transaction import (
            create_transaction, void_transaction,
        )
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            market_day_date='2026-04-29')
        void_transaction(tid, voided_by='Tester')
        n = fresh_db.execute(
            "SELECT COUNT(*) FROM audit_log WHERE table_name="
            "'transactions' AND action='VOID' AND record_id=?",
            (tid,)).fetchone()[0]
        assert n == 1

    def test_market_day_open_close_reopen_all_logged(self, tmp_path):
        from fam.database.connection import (
            close_connection as cc, set_db_path as sd)
        from fam.database.schema import initialize_database as init
        cc()
        sd(str(tmp_path / 'md.db'))
        init()
        from fam.models.market_day import (
            create_market_day, close_market_day, reopen_market_day,
        )
        conn = get_connection()
        conn.execute(
            "INSERT INTO markets (id, name) VALUES (1, 'M')")
        conn.commit()
        md_id = create_market_day(1, '2026-04-29', opened_by='T')
        close_market_day(md_id, closed_by='T')
        reopen_market_day(md_id, opened_by='T')
        actions = {r['action'] for r in conn.execute(
            "SELECT DISTINCT action FROM audit_log "
            " WHERE table_name='market_days'")}
        assert 'OPEN' in actions
        assert 'CLOSE' in actions
        assert 'REOPEN' in actions
        cc()


# ══════════════════════════════════════════════════════════════════
# Settings-table CRUD audit logging (v1.9.10+)
# ══════════════════════════════════════════════════════════════════

class TestSettingsCRUDAreLogged:
    """v1.9.10 closed the audit gaps that the v1.9.9 nightmare
    audit identified.  These tests pin the new contract — vendor
    and payment_method CRUD all write to audit_log."""

    def test_vendor_create_logs_create(self, fresh_db):
        from fam.models.vendor import create_vendor
        vid = create_vendor(name='New Vendor', changed_by='Tester')
        n = fresh_db.execute(
            "SELECT COUNT(*) FROM audit_log "
            " WHERE table_name='vendors' AND action='CREATE' "
            " AND record_id=?", (vid,)).fetchone()[0]
        assert n == 1

    def test_vendor_update_logs_per_field_diff(self, fresh_db):
        from fam.models.vendor import update_vendor
        update_vendor(1, name='Renamed Vendor', city='Pittsburgh',
                       changed_by='Tester')
        rows = fresh_db.execute(
            "SELECT field_name, old_value, new_value FROM audit_log "
            " WHERE table_name='vendors' AND action='UPDATE' "
            " AND record_id=1").fetchall()
        # One row per changed field.
        names = {r['field_name'] for r in rows}
        assert 'name' in names
        assert 'city' in names

    def test_vendor_update_skips_unchanged_fields(self, fresh_db):
        """No-op fields don't pollute the audit log."""
        from fam.models.vendor import update_vendor
        # Same name as existing — should not log.
        existing = fresh_db.execute(
            "SELECT name FROM vendors WHERE id=1").fetchone()[0]
        update_vendor(1, name=existing, changed_by='Tester')
        n = fresh_db.execute(
            "SELECT COUNT(*) FROM audit_log "
            " WHERE table_name='vendors' AND action='UPDATE' "
            " AND record_id=1 AND field_name='name'").fetchone()[0]
        assert n == 0

    def test_payment_method_create_logs(self, fresh_db):
        from fam.models.payment_method import create_payment_method
        pid = create_payment_method(
            name='New PM', match_percent=50.0, sort_order=99,
            changed_by='Tester')
        n = fresh_db.execute(
            "SELECT COUNT(*) FROM audit_log "
            " WHERE table_name='payment_methods' AND action='CREATE' "
            " AND record_id=?", (pid,)).fetchone()[0]
        assert n == 1

    def test_payment_method_update_logs_per_field(self, fresh_db):
        from fam.models.payment_method import update_payment_method
        update_payment_method(
            1, match_percent=75.0, sort_order=42, changed_by='Tester')
        rows = fresh_db.execute(
            "SELECT field_name FROM audit_log "
            " WHERE table_name='payment_methods' AND action='UPDATE' "
            " AND record_id=1").fetchall()
        fields = {r['field_name'] for r in rows}
        assert 'match_percent' in fields
        assert 'sort_order' in fields

    def test_vendor_market_assignment_logged(self, fresh_db):
        from fam.models.vendor import (
            assign_vendor_to_market, unassign_vendor_from_market,
        )
        assign_vendor_to_market(1, 1, changed_by='Tester')
        n_assign = fresh_db.execute(
            "SELECT COUNT(*) FROM audit_log "
            " WHERE table_name='market_vendors' AND action='ASSIGN'"
        ).fetchone()[0]
        assert n_assign >= 1
        unassign_vendor_from_market(1, 1, changed_by='Tester')
        n_unassign = fresh_db.execute(
            "SELECT COUNT(*) FROM audit_log "
            " WHERE table_name='market_vendors' AND action='UNASSIGN'"
        ).fetchone()[0]
        assert n_unassign == 1

    def test_vendor_method_eligibility_logged(self, fresh_db):
        from fam.models.payment_method import (
            assign_payment_method_to_vendor,
            unassign_payment_method_from_vendor,
        )
        # v2.0.7: SNAP and Cash are universal — refuse to unassign,
        # so the existing assert-UNASSIGN-logged check needs a
        # non-universal method.  Add Food Bucks (id=2, denom $2)
        # which is configurable per-vendor.
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            " sort_order, is_active, denomination) VALUES "
            "(2, 'JH Food Bucks', 100.0, 2, 1, 200)")
        fresh_db.commit()
        assign_payment_method_to_vendor(1, 2, changed_by='Tester')
        unassign_payment_method_from_vendor(1, 2, changed_by='Tester')
        actions = {r['action'] for r in fresh_db.execute(
            "SELECT action FROM audit_log "
            " WHERE table_name='vendor_payment_methods'").fetchall()}
        assert 'ASSIGN' in actions
        assert 'UNASSIGN' in actions


# ══════════════════════════════════════════════════════════════════
# Remaining intentional gaps (acceptable for v1.9.10)
# ══════════════════════════════════════════════════════════════════

class TestRemainingNonFinancialGaps:
    """Low-level app_settings key-value writes are intentionally
    NOT audit-logged.  These don't affect transactions; the
    settings UI is the source of truth.  This test pins the
    decision so a future contributor doesn't silently change it.
    """

    def test_settings_changes_do_not_log(self, fresh_db):
        from fam.utils.app_settings import set_setting
        set_setting('test_audit_marker', 'value-A')
        set_setting('test_audit_marker', 'value-B')
        n = fresh_db.execute(
            "SELECT COUNT(*) FROM audit_log "
            " WHERE table_name='app_settings'").fetchone()[0]
        assert n == 0, (
            "app_settings writes now log to audit_log — if this is "
            "intentional, move the test to TestSettingsCRUDAreLogged "
            "and remove this entry.")
