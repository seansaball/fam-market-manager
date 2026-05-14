"""Customer-side denomination forfeit (Phase B) is persisted to
the database AND surfaced as its own column in Vendor
Reimbursement and Detailed Ledger reports
(v2.0.7 schema v36, 2026-05-07).

User-reported scenario: 1 Food RX token ($10 face value) handed
over for a $6.52 single-vendor receipt.  Phase B forfeit produces:

    customer_charged       = $6.52  (effective vendor contribution)
    match_amount           = $0.00  (FAM match fully forfeited)
    method_amount          = $6.52  (vendor reimbursement)
    customer_forfeit_cents = $3.48  (token value not reaching vendor)

The forfeit field tracks the gap between what the customer
physically handed over (the full $10 token) and what reached the
vendor ($6.52).  Pre-v36 this field was computed in-memory by the
engine but never persisted to the DB — reports could not show it
and the AdjustmentDialog re-open could not reconstruct the
original physical token count.

Vendor Reimbursement contract is unchanged: ``Total Due to
Vendor`` = ``SUM(receipt_total)``.  The new column lets reports
surface the customer-side forfeit as a separate signal WITHOUT
shifting any vendor's check amount (which would confuse end-of-
month reconciliation).

Phase A (FAM match reduction without token-value loss) is NOT
counted here — those are unused FAM funds that never existed in
customer hands.  Only Phase B (token-value forfeit) lands in the
column.

This file pins:

  1. **Schema** — column exists with correct shape.
  2. **Migration** — v35→v36 adds the column to legacy tables;
     idempotent.
  3. **Save round-trip** — engine output's customer_forfeit_cents
     is persisted, readable, identical.
  4. **Vendor Reimbursement** — Customer Forfeit column appears
     in the collected output and reflects per-vendor sums.
  5. **Detailed Ledger** — Customer Forfeit column appears per
     transaction.
  6. **External FMNP entries** — Customer Forfeit always 0 (the
     vendor matched the check directly at the booth; no
     denomination forfeit applies).
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def fresh_db(tmp_path):
    db_file = str(tmp_path / "forfeit_persistence.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield get_connection()
    close_connection()


# ──────────────────────────────────────────────────────────────────
# 1. Schema
# ──────────────────────────────────────────────────────────────────


class TestSchemaColumn:

    def test_payment_line_items_has_customer_forfeit_cents(self, fresh_db):
        """The CREATE TABLE definition (and post-migration ALTER)
        must produce a ``customer_forfeit_cents`` column on the
        ``payment_line_items`` table."""
        cols = {row[1] for row in fresh_db.execute(
            "PRAGMA table_info(payment_line_items)").fetchall()}
        assert 'customer_forfeit_cents' in cols, (
            "payment_line_items must include "
            "customer_forfeit_cents (schema v36+).  Got columns: "
            f"{sorted(cols)}")

    def test_column_default_is_zero(self, fresh_db):
        """Pre-v36 rows were inserted without forfeit metadata; the
        default-on-add must be 0 so legacy data has a sensible
        non-NULL value."""
        cols_info = fresh_db.execute(
            "PRAGMA table_info(payment_line_items)").fetchall()
        forfeit_col = next(
            r for r in cols_info if r[1] == 'customer_forfeit_cents')
        # column[3] is "notnull", column[4] is "dflt_value"
        assert forfeit_col[3] == 1, (
            "customer_forfeit_cents must be NOT NULL")
        # SQLite stores numeric default as the string "0"
        assert str(forfeit_col[4]) == '0'

    def test_current_schema_version_is_at_least_36(self):
        """Pin that v36 (customer_forfeit_cents) is in place.
        Asserting >= 36 instead of == 36 lets future schema
        bumps (e.g. v37 user_capped) land without churning this
        test — its purpose is to ensure the forfeit column was
        added, not to police the latest version number."""
        from fam.database.schema import CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION >= 36


# ──────────────────────────────────────────────────────────────────
# 2. Migration v35 → v36 idempotent
# ──────────────────────────────────────────────────────────────────


class TestMigrationV35ToV36:

    def test_migration_function_exists(self):
        from fam.database.schema import _migrate_v35_to_v36
        assert callable(_migrate_v35_to_v36)

    def test_migration_is_idempotent(self, fresh_db):
        """Re-running the migration after the column exists must
        be a no-op rather than ``ALTER TABLE`` failing on the
        duplicate column."""
        from fam.database.schema import _migrate_v35_to_v36
        _migrate_v35_to_v36(fresh_db)
        _migrate_v35_to_v36(fresh_db)
        cols = {row[1] for row in fresh_db.execute(
            "PRAGMA table_info(payment_line_items)").fetchall()}
        assert 'customer_forfeit_cents' in cols

    def test_migration_backfills_legacy_table(self, tmp_path):
        """A pre-v36 database (column absent) gets the column added
        by the migration with all existing rows defaulted to 0."""
        import sqlite3
        from fam.database.schema import _migrate_v35_to_v36

        db_file = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        # Build a minimal pre-v36 payment_line_items table.
        conn.execute("""
            CREATE TABLE payment_line_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id INTEGER NOT NULL,
                payment_method_id INTEGER NOT NULL,
                method_name_snapshot TEXT NOT NULL,
                match_percent_snapshot REAL NOT NULL,
                method_amount INTEGER NOT NULL,
                match_amount INTEGER NOT NULL,
                customer_charged INTEGER NOT NULL,
                photo_path TEXT,
                photo_drive_url TEXT,
                created_at TEXT
            )""")
        conn.execute(
            "INSERT INTO payment_line_items "
            "(transaction_id, payment_method_id, method_name_snapshot, "
            "match_percent_snapshot, method_amount, match_amount, "
            "customer_charged) VALUES "
            "(1, 1, 'SNAP', 100.0, 1000, 500, 500)")
        conn.commit()

        _migrate_v35_to_v36(conn)
        row = conn.execute(
            "SELECT customer_forfeit_cents FROM "
            "payment_line_items WHERE id=1").fetchone()
        assert row[0] == 0, (
            "Migration must backfill existing rows with "
            "customer_forfeit_cents=0.")
        conn.close()


# ──────────────────────────────────────────────────────────────────
# 3. Save round-trip
# ──────────────────────────────────────────────────────────────────


class TestSaveRoundTrip:

    def test_forfeit_persisted_to_db(self, fresh_db):
        """The user's exact reproducer: 1 Food RX token to a
        $6.52 receipt.  Save the engine's output (including
        customer_forfeit_cents=348) and verify the DB stores it."""
        from fam.models.transaction import (
            create_transaction, save_payment_line_items, confirm_transaction,
        )
        from fam.models.customer_order import create_customer_order
        fresh_db.execute(
            "INSERT INTO markets (id, name, daily_match_limit, "
            "match_limit_active) VALUES (1, 'M', 100000, 1)")
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            "denomination, sort_order, is_active) VALUES "
            "(1, 'Food RX', 100.0, 1000, 1, 1)")
        fresh_db.execute(
            "INSERT INTO market_payment_methods (market_id, "
            "payment_method_id) VALUES (1, 1)")
        fresh_db.execute(
            "INSERT INTO vendors (id, name) VALUES (1, 'Fungetarian')")
        fresh_db.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, 1)")
        fresh_db.execute(
            "INSERT INTO vendor_payment_methods (vendor_id, "
            "payment_method_id) VALUES (1, 1)")
        fresh_db.execute(
            "INSERT INTO market_days (id, market_id, date, "
            "status, opened_by) VALUES "
            "(1, 1, '2099-05-01', 'Open', 'Tester')")
        fresh_db.commit()

        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-001-LB1')
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=652,
            customer_order_id=order_id,
            market_day_date='2099-05-01')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1,
            'method_name_snapshot': 'Food RX',
            'match_percent_snapshot': 100.0,
            'method_amount': 652,
            'match_amount': 0,
            'customer_charged': 652,
            'customer_forfeit_cents': 348,
            'photo_path': None,
        }])
        confirm_transaction(txn_id, confirmed_by='Tester')

        row = fresh_db.execute(
            "SELECT customer_charged, match_amount, method_amount, "
            "customer_forfeit_cents FROM payment_line_items "
            "WHERE transaction_id=?", (txn_id,)).fetchone()
        assert row['customer_charged'] == 652
        assert row['match_amount'] == 0
        assert row['method_amount'] == 652
        assert row['customer_forfeit_cents'] == 348, (
            f"customer_forfeit_cents must round-trip through save.  "
            f"Got: {row['customer_forfeit_cents']} (expected 348).")

    def test_default_zero_when_omitted(self, fresh_db):
        """Save path should accept items dicts that don't carry the
        forfeit key (legacy callers, non-denom rows) and default to
        0 in the DB."""
        from fam.models.transaction import (
            create_transaction, save_payment_line_items, confirm_transaction,
        )
        from fam.models.customer_order import create_customer_order
        fresh_db.execute(
            "INSERT INTO markets (id, name, daily_match_limit, "
            "match_limit_active) VALUES (1, 'M', 100000, 1)")
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            "denomination, sort_order, is_active) VALUES "
            "(1, 'SNAP', 100.0, NULL, 1, 1)")
        fresh_db.execute(
            "INSERT INTO market_payment_methods (market_id, "
            "payment_method_id) VALUES (1, 1)")
        fresh_db.execute(
            "INSERT INTO vendors (id, name) VALUES (1, 'V')")
        fresh_db.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, 1)")
        fresh_db.execute(
            "INSERT INTO vendor_payment_methods (vendor_id, "
            "payment_method_id) VALUES (1, 1)")
        fresh_db.execute(
            "INSERT INTO market_days (id, market_id, date, "
            "status, opened_by) VALUES "
            "(1, 1, '2099-05-01', 'Open', 'T')")
        fresh_db.commit()

        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-002-LB1')
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=1000,
            customer_order_id=order_id,
            market_day_date='2099-05-01')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1,
            'method_name_snapshot': 'SNAP',
            'match_percent_snapshot': 100.0,
            'method_amount': 1000,
            'match_amount': 500,
            'customer_charged': 500,
            # NO customer_forfeit_cents key — legacy caller shape.
            'photo_path': None,
        }])
        confirm_transaction(txn_id, confirmed_by='T')

        row = fresh_db.execute(
            "SELECT customer_forfeit_cents FROM "
            "payment_line_items WHERE transaction_id=?",
            (txn_id,)).fetchone()
        assert row['customer_forfeit_cents'] == 0


# ──────────────────────────────────────────────────────────────────
# 4. Vendor Reimbursement report column
# ──────────────────────────────────────────────────────────────────


class TestVendorReimbursementColumn:

    def test_customer_forfeit_column_in_output(self, fresh_db):
        from fam.models.transaction import (
            create_transaction, save_payment_line_items, confirm_transaction,
        )
        from fam.models.customer_order import create_customer_order
        from fam.sync.data_collector import _collect_vendor_reimbursement
        fresh_db.execute(
            "INSERT INTO markets (id, name, daily_match_limit, "
            "match_limit_active) VALUES (1, 'M', 100000, 1)")
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            "denomination, sort_order, is_active) VALUES "
            "(1, 'Food RX', 100.0, 1000, 1, 1)")
        fresh_db.execute(
            "INSERT INTO market_payment_methods (market_id, "
            "payment_method_id) VALUES (1, 1)")
        fresh_db.execute(
            "INSERT INTO vendors (id, name) VALUES (1, 'Fungetarian')")
        fresh_db.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, 1)")
        fresh_db.execute(
            "INSERT INTO vendor_payment_methods (vendor_id, "
            "payment_method_id) VALUES (1, 1)")
        fresh_db.execute(
            "INSERT INTO market_days (id, market_id, date, "
            "status, opened_by) VALUES "
            "(1, 1, '2099-05-01', 'Open', 'T')")
        fresh_db.commit()
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-001-LB1')
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=652,
            customer_order_id=order_id,
            market_day_date='2099-05-01')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1,
            'method_name_snapshot': 'Food RX',
            'match_percent_snapshot': 100.0,
            'method_amount': 652,
            'match_amount': 0,
            'customer_charged': 652,
            'customer_forfeit_cents': 348,
            'photo_path': None,
        }])
        confirm_transaction(txn_id, confirmed_by='T')

        rows = _collect_vendor_reimbursement(fresh_db, [1])
        assert len(rows) == 1
        r = rows[0]
        assert 'Customer Forfeit' in r, (
            f"Vendor Reimbursement output must include 'Customer "
            f"Forfeit' column.  Got keys: {sorted(r.keys())}")
        assert r['Customer Forfeit'] == 3.48, (
            f"Customer Forfeit for the user's scenario must be "
            f"$3.48.  Got: ${r['Customer Forfeit']:.2f}")
        # Vendor reimbursement contract still holds — vendor gets
        # the receipt total, not the token face value.
        assert r['Total Due to Vendor'] == 6.52
        # v2.0.7+ denomination-integrity: per-method columns now
        # show the customer's actual denomination payment
        # (customer_charged + forfeit), so Food RX = $10 here, NOT
        # $6.52.  Math identity:
        #   Σ(method-cols) + FAM Match - Customer Forfeit
        #     + FMNP (External) = Total Due to Vendor
        method_sum = sum(
            v for k, v in r.items()
            if k not in {'Market Name', 'Vendor', 'Month',
                          'Year-Month', 'Date(s)',
                          'Total Due to Vendor', 'FAM Match',
                          'FMNP (External)', 'Customer Forfeit',
                          'Check Payable To', 'Address'})
        assert abs(
            method_sum + r['FAM Match'] - r['Customer Forfeit']
            + r['FMNP (External)'] - r['Total Due to Vendor']
        ) <= 0.01, (
            "Σ(method-cols) + FAM Match - Customer Forfeit + "
            "FMNP (External) must equal Total Due to Vendor "
            "(within penny rec)")
        # method_sum IS the customer's physical handout
        # (denomination-true): 1 × $10 token = $10.
        assert abs(method_sum - 10.00) <= 0.01, (
            f"Per-method columns must sum to the customer's "
            f"denomination-true payment ($10).  Got: ${method_sum:.2f}")


# ──────────────────────────────────────────────────────────────────
# 5. Detailed Ledger report column
# ──────────────────────────────────────────────────────────────────


class TestDetailedLedgerColumn:

    def test_customer_forfeit_column_in_output(self, fresh_db):
        from fam.models.transaction import (
            create_transaction, save_payment_line_items, confirm_transaction,
        )
        from fam.models.customer_order import create_customer_order
        from fam.sync.data_collector import _collect_detailed_ledger
        fresh_db.execute(
            "INSERT INTO markets (id, name, daily_match_limit, "
            "match_limit_active) VALUES (1, 'M', 100000, 1)")
        fresh_db.execute(
            "INSERT INTO payment_methods (id, name, match_percent, "
            "denomination, sort_order, is_active) VALUES "
            "(1, 'Food RX', 100.0, 1000, 1, 1)")
        fresh_db.execute(
            "INSERT INTO market_payment_methods (market_id, "
            "payment_method_id) VALUES (1, 1)")
        fresh_db.execute(
            "INSERT INTO vendors (id, name) VALUES (1, 'V')")
        fresh_db.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, 1)")
        fresh_db.execute(
            "INSERT INTO vendor_payment_methods (vendor_id, "
            "payment_method_id) VALUES (1, 1)")
        fresh_db.execute(
            "INSERT INTO market_days (id, market_id, date, "
            "status, opened_by) VALUES "
            "(1, 1, '2099-05-01', 'Open', 'T')")
        fresh_db.commit()
        order_id, _ = create_customer_order(
            market_day_id=1, customer_label='C-001-LB1')
        txn_id, _ = create_transaction(
            market_day_id=1, vendor_id=1, receipt_total=652,
            customer_order_id=order_id,
            market_day_date='2099-05-01')
        save_payment_line_items(txn_id, [{
            'payment_method_id': 1,
            'method_name_snapshot': 'Food RX',
            'match_percent_snapshot': 100.0,
            'method_amount': 652,
            'match_amount': 0,
            'customer_charged': 652,
            'customer_forfeit_cents': 348,
            'photo_path': None,
        }])
        confirm_transaction(txn_id, confirmed_by='T')

        rows = _collect_detailed_ledger(fresh_db, 1)
        # First row should be the txn we just created
        txn_rows = [r for r in rows if r.get('Status') == 'Confirmed']
        assert len(txn_rows) == 1
        r = txn_rows[0]
        assert 'Customer Forfeit' in r, (
            f"Detailed Ledger output must include 'Customer "
            f"Forfeit' column.  Got keys: {sorted(r.keys())}")
        # v2.0.7+ denomination-integrity: Customer Paid now reflects
        # the customer's actual denomination payment (= customer_charged
        # + customer_forfeit_cents).  For 1 × $10 Food RX → $6.52
        # receipt: customer paid $10, forfeited $3.48, vendor got $6.52.
        # Math identity: Customer Paid + FAM Match - Customer Forfeit
        # = Receipt Total → $10.00 + $0.00 - $3.48 = $6.52 ✓
        assert r['Customer Forfeit'] == 3.48
        assert r['Customer Paid'] == 10.00, (
            f"Detailed Ledger Customer Paid must reflect the "
            f"customer's denomination-true payment ($10), not the "
            f"post-forfeit value.  Got: ${r['Customer Paid']:.2f}")
        assert r['FAM Match'] == 0
        assert r['Receipt Total'] == 6.52
        # Reconciliation across the row.
        recon = (r['Customer Paid'] + r['FAM Match']
                 - r['Customer Forfeit'])
        assert abs(recon - r['Receipt Total']) <= 0.01, (
            f"Customer Paid + FAM Match - Customer Forfeit "
            f"({recon:.2f}) must equal Receipt Total "
            f"({r['Receipt Total']:.2f})")

    def test_external_fmnp_entries_have_zero_forfeit(self, fresh_db):
        """External FMNP entries are vendor-direct — no
        denomination forfeit semantics apply."""
        from fam.sync.data_collector import _collect_detailed_ledger
        fresh_db.execute(
            "INSERT INTO markets (id, name, daily_match_limit, "
            "match_limit_active) VALUES (1, 'M', 100000, 1)")
        fresh_db.execute(
            "INSERT INTO vendors (id, name) VALUES (1, 'V')")
        fresh_db.execute(
            "INSERT INTO market_days (id, market_id, date, "
            "status, opened_by) VALUES "
            "(1, 1, '2099-05-01', 'Open', 'T')")
        fresh_db.execute(
            "INSERT INTO fmnp_entries (id, market_day_id, "
            "vendor_id, amount, status, entered_by, created_at) "
            "VALUES (1, 1, 1, 1500, 'Active', 'T', "
            "'2099-05-01 10:00:00')")
        fresh_db.commit()

        rows = _collect_detailed_ledger(fresh_db, 1)
        fmnp_rows = [r for r in rows if r.get('Status') == 'FMNP Entry']
        assert len(fmnp_rows) == 1
        assert fmnp_rows[0]['Customer Forfeit'] == 0
