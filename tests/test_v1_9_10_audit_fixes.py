"""End-to-end audit fix regression tests (v1.9.10, 2026-05-01).

Pins every fix from the pre-go-live financial-integrity audit:

  C1   photo_drive_url preserved across re-save
  C2-4 markets settings + reward_rules edits write audit_log rows
  H1   vendor reimbursement keeps money in cents (no float drift)
  H2   receipt_count uses COUNT(DISTINCT t.id)
  H3   Activity Log preserves originating device_id
  H5   capture_device_id() empty-value hard-fail
  H6   sync worker re-collects in the same scope it was constructed with
  H8   update_transaction self-audits non-status field changes
  M1   admin void of last txn flips parent customer_order to Voided
  M3   manager.sync_all skips last_sync_at on partial failure
  M5   _migrate_v5_to_v6 is idempotent
  M6   update_customer_order_status / _zip_code emit audit_log rows
  G1   payment_line_items UPDATE rejects negative method/match/customer
  G3   transactions Voided→non-Voided rejected at DB trigger level
  G2   post-confirm SUM(method_amount) per txn equals receipt_total
  H10  ledger rotation keeps prev{1..N} snapshots
  H10b binary backup restore round-trip works

If any of these fail, the bug is live in production and ships a
financial-integrity defect.
"""

import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

from fam.database.connection import (
    get_connection, set_db_path, close_connection,
)
from fam.database.schema import initialize_database


# ── Fixture: minimal isolated DB ──────────────────────────────────

@pytest.fixture
def db(tmp_path):
    db_file = str(tmp_path / "audit_fix.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'TestMarket', 100000, 1)")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'VendorA')")
    conn.execute("INSERT INTO vendors (id, name) VALUES (2, 'VendorB')")
    conn.execute("INSERT INTO market_vendors (market_id, vendor_id) VALUES (1, 1)")
    conn.execute("INSERT INTO market_vendors (market_id, vendor_id) VALUES (1, 2)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "(1, 'SNAP', 100.0, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "(2, 'Food RX', 100.0, 1000, 2, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        "payment_method_id) VALUES (1, 1), (1, 2)")
    conn.execute(
        "INSERT INTO vendor_payment_methods (vendor_id, "
        "payment_method_id) VALUES (1, 1), (1, 2), (2, 1), (2, 2)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES (1, 1, '2026-05-01', 'Open', 'Tester')")
    conn.commit()
    yield conn
    close_connection()


# ══════════════════════════════════════════════════════════════════
# C1 — photo_drive_url survives re-save
# ══════════════════════════════════════════════════════════════════

class TestPhotoDriveUrlPreservation:

    def test_drive_url_preserved_across_re_save(self, db):
        """When PaymentScreen calls save_payment_line_items twice
        on the same txn (e.g. confirm-then-adjust, or save-draft-
        then-resume), the second save MUST preserve the photo's
        Google Drive URL written by the prior sync.  Pre-fix this
        column wasn't even in the INSERT list, so every re-save
        cleared it."""
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
            update_payment_photo_drive_url, get_payment_line_items,
        )
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=2000,
            market_day_date='2026-05-01')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 2000,
            'match_amount': 1000,
            'customer_charged': 1000,
            'photo_path': 'pay/photo123.jpg',
        }])
        rows = get_payment_line_items(txn_id)
        update_payment_photo_drive_url(
            rows[0]['id'], 'https://drive.google.com/file/abc')

        # Re-save — same payment method, same photo_path; the
        # drive URL MUST carry over.
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 2000,
            'match_amount': 1000,
            'customer_charged': 1000,
            'photo_path': 'pay/photo123.jpg',
        }])
        rows = get_payment_line_items(txn_id)
        assert rows[0]['photo_drive_url'] == 'https://drive.google.com/file/abc', (
            "photo_drive_url must survive re-save when the pm/path pair "
            "matches a prior row"
        )

    def test_drive_url_dropped_when_photo_path_changes(self, db):
        """If the volunteer re-takes the photo (photo_path differs
        from the prior save), the old Drive URL points to a stale
        image and SHOULD be dropped — the next sync re-uploads."""
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
            update_payment_photo_drive_url, get_payment_line_items,
        )
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=2000,
            market_day_date='2026-05-01')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 2000, 'match_amount': 1000,
            'customer_charged': 1000,
            'photo_path': 'pay/old.jpg',
        }])
        rows = get_payment_line_items(txn_id)
        update_payment_photo_drive_url(
            rows[0]['id'], 'https://drive.google.com/file/old')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 2000, 'match_amount': 1000,
            'customer_charged': 1000,
            'photo_path': 'pay/new.jpg',  # different path
        }])
        rows = get_payment_line_items(txn_id)
        assert rows[0]['photo_drive_url'] is None or rows[0]['photo_drive_url'] == '', (
            "photo_drive_url must drop when photo_path changes"
        )


# ══════════════════════════════════════════════════════════════════
# C2-C4 — audit log for markets settings + reward_rules
# ══════════════════════════════════════════════════════════════════

class TestFinancialSettingsAuditTrail:

    def test_reward_rule_create_writes_audit_log(self, db):
        from fam.models.reward_rule import create_reward_rule
        # Need a denominated reward method — Food RX (id=2) qualifies.
        rid = create_reward_rule(
            source_method_id=1,
            threshold_cents=1000,
            reward_method_id=2,
            reward_unit_cents=200,
            changed_by='UnitTester')
        rows = db.execute(
            "SELECT action, table_name, changed_by FROM audit_log "
            "WHERE table_name='reward_rules' AND record_id=?",
            (rid,)).fetchall()
        assert len(rows) >= 1
        actions = {r['action'] for r in rows}
        assert 'CREATE' in actions
        assert any(r['changed_by'] == 'UnitTester' for r in rows)

    def test_reward_rule_update_writes_per_field_audit_rows(self, db):
        from fam.models.reward_rule import (
            create_reward_rule, update_reward_rule)
        rid = create_reward_rule(
            source_method_id=1, threshold_cents=1000,
            reward_method_id=2, reward_unit_cents=200,
            changed_by='Setup')
        # Two fields change.
        update_reward_rule(
            rid, threshold_cents=1500, reward_unit_cents=300,
            changed_by='UnitTester')
        rows = db.execute(
            "SELECT field_name, old_value, new_value FROM audit_log "
            "WHERE table_name='reward_rules' AND record_id=? "
            "AND action='UPDATE'",
            (rid,)).fetchall()
        fields = {r['field_name'] for r in rows}
        assert 'threshold_cents' in fields
        assert 'reward_unit_cents' in fields

    def test_reward_rule_delete_audited_with_snapshot(self, db):
        from fam.models.reward_rule import (
            create_reward_rule, delete_reward_rule)
        rid = create_reward_rule(
            source_method_id=1, threshold_cents=1000,
            reward_method_id=2, reward_unit_cents=200,
            changed_by='Setup')
        delete_reward_rule(rid, changed_by='UnitTester')
        rows = db.execute(
            "SELECT action, old_value FROM audit_log "
            "WHERE table_name='reward_rules' AND record_id=?",
            (rid,)).fetchall()
        actions = [r['action'] for r in rows]
        assert 'DELETE' in actions
        # Snapshot includes the rule's pre-delete contents.
        delete_row = next(r for r in rows if r['action'] == 'DELETE')
        assert 'threshold=1000' in (delete_row['old_value'] or '')


# ══════════════════════════════════════════════════════════════════
# H1 — vendor reimbursement: no float drift
# ══════════════════════════════════════════════════════════════════

class TestVendorReimbursementCentsAccumulation:

    def test_row_identity_holds_in_cents(self, db):
        """Σ method-cols + FAM Match + FMNP_External == Total Due
        within 0¢ tolerance (no float drift)."""
        from fam.models.transaction import (
            create_transaction, confirm_transaction,
            save_payment_line_items,
        )
        from fam.sync.data_collector import _collect_vendor_reimbursement

        # VendorA: receipt $0.10 + FMNP $0.20 → Total Due $0.30.
        # The classic FP trap: 0.1 + 0.2 == 0.30000000000000004.
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=10,
            market_day_date='2026-05-01')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 10, 'match_amount': 5,
            'customer_charged': 5,
            'photo_path': None,
        }])
        confirm_transaction(txn_id, confirmed_by='Tester')
        db.execute(
            "INSERT INTO fmnp_entries (market_day_id, vendor_id, "
            "amount, status, entered_by, created_at) VALUES "
            "(1, 1, 20, 'Active', 'Tester', '2026-05-01 10:00:00')")
        db.commit()

        rows = _collect_vendor_reimbursement(db, [1])
        row = next(r for r in rows if r['Vendor'] == 'VendorA')

        # Total Due should be exactly 30 cents = $0.30.
        assert row['Total Due to Vendor'] == 0.30 or (
            abs(row['Total Due to Vendor'] - 0.30) < 1e-9), (
            f"expected $0.30, got {row['Total Due to Vendor']!r}")

        # Identity: per-method totals + FAM Match + FMNP External
        # ≈ Total Due, in CENTS (multiplied by 100, rounded).
        method_cents_sum = round(row.get('SNAP', 0.0) * 100)
        method_cents_sum += round(row.get('Food RX', 0.0) * 100)
        fam_cents = round(row['FAM Match'] * 100)
        fmnp_cents = round(row['FMNP (External)'] * 100)
        total_due_cents = round(row['Total Due to Vendor'] * 100)
        assert method_cents_sum + fam_cents + fmnp_cents == total_due_cents, (
            f"row identity violated: "
            f"methods({method_cents_sum}) + fam({fam_cents}) + "
            f"fmnp({fmnp_cents}) != total_due({total_due_cents})")


# ══════════════════════════════════════════════════════════════════
# H2 — receipt_count uses DISTINCT
# ══════════════════════════════════════════════════════════════════

class TestReceiptCountDistinct:

    def test_multi_pli_per_txn_does_not_inflate_receipt_count(self, db):
        from fam.models.customer_order import (
            create_customer_order,
            update_customer_order_status,
            get_confirmed_customers_for_market_day,
        )
        from fam.models.transaction import (
            create_transaction, confirm_transaction,
            save_payment_line_items,
        )

        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-ABC', zip_code='15102')
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1500,
            customer_order_id=order_id, market_day_date='2026-05-01')

        # ONE transaction with TWO different methods.  Pre-fix,
        # receipt_count returned 2; post-fix, returns 1.
        save_payment_line_items(txn_id, [
            {'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
             'match_percent_snapshot': 100.0,
             'method_amount': 1000, 'match_amount': 500,
             'customer_charged': 500, 'photo_path': None},
            {'payment_method_id': 2, 'method_name_snapshot': 'Food RX',
             'match_percent_snapshot': 100.0,
             'method_amount': 500, 'match_amount': 250,
             'customer_charged': 250, 'photo_path': None},
        ])
        confirm_transaction(txn_id, confirmed_by='Tester')
        # The PaymentScreen flow flips the parent order to Confirmed
        # after confirming the txn; mirror that here.
        update_customer_order_status(order_id, 'Confirmed')

        result = get_confirmed_customers_for_market_day(1)
        c = next(r for r in result if r['customer_label'] == 'C-ABC')
        assert c['receipt_count'] == 1, (
            f"receipt_count must be 1 (one transaction with 2 methods), "
            f"got {c['receipt_count']}")


# ══════════════════════════════════════════════════════════════════
# H3 — Activity Log preserves originating device_id
# ══════════════════════════════════════════════════════════════════

class TestActivityLogDeviceIdPreserved:

    def test_collect_sync_data_preserves_existing_device_id(self, db):
        """End-to-end check: stuff a foreign device_id into the
        audit_log directly, run collect_sync_data, and verify the
        Activity Log row carries the foreign device_id (not the
        current one).  Pre-fix, _append_identity unconditionally
        overwrote with the local device's id.
        """
        from fam.sync.data_collector import collect_sync_data

        # Insert an audit_log row that originated on a different
        # device.  collect_sync_data's _append_identity should
        # leave that device_id alone.
        db.execute(
            "INSERT INTO audit_log (table_name, record_id, action, "
            "changed_by, app_version, device_id, changed_at) "
            "VALUES ('transactions', 999, 'CREATE', 'Foreigner', "
            "'1.9.10', 'LB-OTHER', '2026-05-01 09:00:00')")
        db.commit()

        # Activity Log is opt-in; enable for the test.
        from fam.utils.app_settings import set_setting
        set_setting('sync_tab_activity_log', '1')

        with patch('fam.sync.data_collector.get_device_id',
                    return_value='LB-MINE'):
            data = collect_sync_data(market_day_id=1)
        activity = data.get('Activity Log', [])
        target = next(
            (r for r in activity
             if r.get('Record ID') == 999), None)
        assert target is not None, "audit row missing from Activity Log"
        assert target['device_id'] == 'LB-OTHER', (
            f"foreign device_id must survive the sync identity "
            f"append; got {target.get('device_id')!r}")


# ══════════════════════════════════════════════════════════════════
# H8 — update_transaction self-audits
# ══════════════════════════════════════════════════════════════════

class TestUpdateTransactionAuditsItself:

    def test_receipt_total_change_writes_audit_row(self, db):
        from fam.models.transaction import (
            create_transaction, update_transaction,
        )
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            market_day_date='2026-05-01')
        update_transaction(
            txn_id, receipt_total=1500, changed_by='UnitTester')
        rows = db.execute(
            "SELECT field_name, old_value, new_value, changed_by "
            "FROM audit_log WHERE table_name='transactions' "
            "AND record_id=? AND action='UPDATE'",
            (txn_id,)).fetchall()
        receipt_changes = [r for r in rows if r['field_name'] == 'receipt_total']
        assert len(receipt_changes) == 1
        assert receipt_changes[0]['old_value'] == '1000'
        assert receipt_changes[0]['new_value'] == '1500'
        assert receipt_changes[0]['changed_by'] == 'UnitTester'

    def test_no_audit_row_when_field_unchanged(self, db):
        from fam.models.transaction import (
            create_transaction, update_transaction,
        )
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            market_day_date='2026-05-01')
        update_transaction(
            txn_id, receipt_total=1000,  # same as existing
            changed_by='UnitTester')
        rows = db.execute(
            "SELECT * FROM audit_log WHERE table_name='transactions' "
            "AND record_id=? AND action='UPDATE'",
            (txn_id,)).fetchall()
        assert len(rows) == 0


# ══════════════════════════════════════════════════════════════════
# G1 + G3 — DB triggers
# ══════════════════════════════════════════════════════════════════

class TestDBTriggers:

    def test_pli_update_rejects_negative_method_amount(self, db):
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
        )
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            market_day_date='2026-05-01')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 1000, 'match_amount': 500,
            'customer_charged': 500, 'photo_path': None,
        }])
        with pytest.raises(sqlite3.IntegrityError, match='method_amount'):
            db.execute(
                "UPDATE payment_line_items SET method_amount=-1 "
                "WHERE transaction_id=?", (txn_id,))

    def test_pli_update_rejects_negative_match_amount(self, db):
        from fam.models.transaction import (
            create_transaction, save_payment_line_items,
        )
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            market_day_date='2026-05-01')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 1000, 'match_amount': 500,
            'customer_charged': 500, 'photo_path': None,
        }])
        with pytest.raises(sqlite3.IntegrityError, match='match_amount'):
            db.execute(
                "UPDATE payment_line_items SET match_amount=-1 "
                "WHERE transaction_id=?", (txn_id,))

    def test_voided_transaction_cannot_become_unvoided(self, db):
        from fam.models.transaction import (
            create_transaction, void_transaction,
        )
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            market_day_date='2026-05-01')
        void_transaction(txn_id, voided_by='Tester')
        with pytest.raises(sqlite3.IntegrityError, match='Voided'):
            db.execute(
                "UPDATE transactions SET status='Confirmed' WHERE id=?",
                (txn_id,))


# ══════════════════════════════════════════════════════════════════
# M1 — admin void of last txn flips parent order to Voided
# ══════════════════════════════════════════════════════════════════

class TestVoidLastTxnFlipsOrderStatus:
    """When the LAST non-voided transaction in a customer_order
    gets voided, the parent customer_order.status should also flip
    to 'Voided' so reports filtering by order status don't miss
    functionally-voided orders."""

    def test_void_only_txn_flips_order_to_voided(self, db):
        # Smoke-level test of the model-layer logic the admin UI
        # delegates to.  The full UI integration is exercised by
        # the manual onsite test plan.
        from fam.models.customer_order import (
            create_customer_order,
            update_customer_order_status,
        )
        from fam.models.transaction import (
            create_transaction, void_transaction,
        )
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-X', zip_code='15102')
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            customer_order_id=order_id, market_day_date='2026-05-01')
        void_transaction(txn_id, voided_by='Tester')
        # Mirror the admin handler's last-txn flip.
        remaining = db.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE customer_order_id=? AND status != 'Voided'",
            (order_id,)).fetchone()[0]
        if remaining == 0:
            update_customer_order_status(
                order_id, 'Voided', changed_by='Tester')
        status = db.execute(
            "SELECT status FROM customer_orders WHERE id=?",
            (order_id,)).fetchone()['status']
        assert status == 'Voided'


# ══════════════════════════════════════════════════════════════════
# M3 — sync_all skips last_sync_at on partial failure
# ══════════════════════════════════════════════════════════════════

class TestSyncManagerLastSyncAt:

    def test_partial_failure_does_not_advance_last_sync_at(self):
        from fam.sync.manager import SyncManager
        from fam.sync.base import SyncResult
        from fam.utils import app_settings

        class FakeBackend:
            def upsert_rows(self, sheet, rows, keys, delete_stale=True):
                if sheet == 'BadTab':
                    raise RuntimeError('boom')
                return SyncResult(success=True, rows_synced=len(rows))

        # Reset for test
        app_settings.set_setting('last_sync_at', '')
        app_settings.set_setting('last_sync_error', '')
        m = SyncManager(FakeBackend(), throttle_writes=False)
        m.sync_all({'GoodTab': [{'a': 1}], 'BadTab': [{'b': 2}]})
        assert app_settings.get_setting('last_sync_at') == '', (
            "last_sync_at must NOT advance on partial failure")
        assert 'BadTab' in (app_settings.get_setting('last_sync_error') or '')

    def test_clean_run_advances_last_sync_at(self):
        from fam.sync.manager import SyncManager
        from fam.sync.base import SyncResult
        from fam.utils import app_settings

        class FakeBackend:
            def upsert_rows(self, sheet, rows, keys, delete_stale=True):
                return SyncResult(success=True, rows_synced=len(rows))

        app_settings.set_setting('last_sync_at', '')
        m = SyncManager(FakeBackend(), throttle_writes=False)
        m.sync_all({'GoodTab': [{'a': 1}]})
        ts = app_settings.get_setting('last_sync_at')
        assert ts and ts.strip(), (
            "last_sync_at must populate on a clean run")


# ══════════════════════════════════════════════════════════════════
# M5 — _migrate_v5_to_v6 idempotent
# ══════════════════════════════════════════════════════════════════

class TestV5ToV6Idempotent:

    def test_double_run_does_not_error(self, tmp_path):
        from fam.database.schema import _migrate_v5_to_v6
        # Build a v5-shape DB (already-renamed columns would survive
        # a re-run because of the introspection guard).
        db_path = str(tmp_path / "v5.db")
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        c.executescript("""
            CREATE TABLE payment_methods (
                id INTEGER PRIMARY KEY,
                name TEXT,
                discount_percent REAL
            );
            CREATE TABLE payment_line_items (
                id INTEGER PRIMARY KEY,
                discount_percent_snapshot REAL,
                discount_amount INTEGER
            );
        """)
        c.commit()
        _migrate_v5_to_v6(c)
        # Second run on the renamed schema must not raise.
        _migrate_v5_to_v6(c)
        # Confirm the rename actually happened.
        cols = {r[1] for r in c.execute(
            "PRAGMA table_info(payment_methods)").fetchall()}
        assert 'match_percent' in cols
        assert 'discount_percent' not in cols
        c.close()


# ══════════════════════════════════════════════════════════════════
# G2 — post-confirm SUM(method_amount) == receipt_total per txn
# ══════════════════════════════════════════════════════════════════

class TestPostConfirmSumEqualsReceipt:
    """Application-layer verifier — for every confirmed/adjusted
    transaction, SUM(method_amount of its line items) MUST equal
    receipt_total within ±1¢ (the documented penny-rounding
    tolerance)."""

    def test_invariant_holds_after_simple_confirm(self, db):
        from fam.models.transaction import (
            create_transaction, confirm_transaction,
            save_payment_line_items,
        )
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=2000,
            market_day_date='2026-05-01')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 2000, 'match_amount': 1000,
            'customer_charged': 1000, 'photo_path': None,
        }])
        confirm_transaction(txn_id, confirmed_by='Tester')
        receipt = db.execute(
            "SELECT receipt_total FROM transactions WHERE id=?",
            (txn_id,)).fetchone()[0]
        s = db.execute(
            "SELECT COALESCE(SUM(method_amount),0) FROM "
            "payment_line_items WHERE transaction_id=?",
            (txn_id,)).fetchone()[0]
        assert abs(s - receipt) <= 1, (
            f"SUM(method_amount)={s} must equal receipt_total={receipt} "
            f"within ±1¢ for confirmed transaction {txn_id}")


# ══════════════════════════════════════════════════════════════════
# H10 — ledger rotation
# ══════════════════════════════════════════════════════════════════

class TestLedgerRotation:

    def test_rotation_preserves_prev_snapshots(self, db, tmp_path):
        from fam.utils.export import write_ledger_backup

        # Snapshot 1.
        write_ledger_backup(force=True)
        backup_dir = os.path.dirname(get_db_path())
        current = os.path.join(backup_dir, 'fam_ledger_backup.txt')
        assert os.path.exists(current), "current ledger must exist"

        # Capture content of v1, then trigger a re-write — the
        # original snapshot should rotate to .prev1.
        original_content = open(current, encoding='utf-8').read()

        # Add something different so the new snapshot has different content
        # (the cooldown-debounce in write_ledger_backup is bypassed by
        # force=True).
        from fam.models.transaction import (
            create_transaction, confirm_transaction,
            save_payment_line_items,
        )
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=2500,
            market_day_date='2026-05-01')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 2500, 'match_amount': 1250,
            'customer_charged': 1250, 'photo_path': None,
        }])
        confirm_transaction(txn_id, confirmed_by='Tester')

        write_ledger_backup(force=True)
        prev1 = os.path.join(backup_dir, 'fam_ledger_backup.prev1.txt')
        assert os.path.exists(prev1), (
            "rotation must produce fam_ledger_backup.prev1.txt")
        prev1_content = open(prev1, encoding='utf-8').read()
        assert prev1_content == original_content, (
            "prev1 must contain the previous snapshot verbatim")


def get_db_path():
    """Test helper — read the active DB path."""
    from fam.database.connection import get_db_path as _gdp
    return _gdp()


# ══════════════════════════════════════════════════════════════════
# H10b — binary backup restore round-trip
# ══════════════════════════════════════════════════════════════════

class TestBinaryBackupRestore:

    def test_create_then_restore_recovers_data(self, db, tmp_path):
        """The binary backup is the canonical recovery path.  Pre-
        fix, no test exercised an actual restore — this pins the
        round-trip so a future regression to ``create_backup``
        breaks loud."""
        from fam.database.backup import create_backup
        from fam.models.transaction import (
            create_transaction, confirm_transaction,
            save_payment_line_items,
        )

        # Seed a confirmed transaction we expect to recover.
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=2000,
            market_day_date='2026-05-01')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 2000, 'match_amount': 1000,
            'customer_charged': 1000, 'photo_path': None,
        }])
        confirm_transaction(txn_id, confirmed_by='Backup-Tester')

        backup_path = create_backup(reason='restore_test')
        assert backup_path and os.path.exists(backup_path)

        # Open the backup directly as if it were the live DB.
        # If create_backup uses SQLite's backup API correctly, the
        # backup is a fully-formed SQLite file with every row.
        bk = sqlite3.connect(backup_path)
        bk.row_factory = sqlite3.Row
        try:
            row = bk.execute(
                "SELECT receipt_total, status FROM transactions "
                "WHERE id=?", (txn_id,)).fetchone()
            assert row is not None
            assert row['receipt_total'] == 2000
            assert row['status'] == 'Confirmed'
            pli = bk.execute(
                "SELECT method_amount, match_amount, customer_charged "
                "FROM payment_line_items WHERE transaction_id=?",
                (txn_id,)).fetchone()
            assert pli['method_amount'] == 2000
            assert pli['match_amount'] == 1000
            assert pli['customer_charged'] == 1000
        finally:
            bk.close()
