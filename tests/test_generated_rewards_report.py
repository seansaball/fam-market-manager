"""Tests for the Generated Rewards report and its cloud sync
collector.

v1.9.10 update: rewards are now a **write-once snapshot history**
(table ``generated_rewards``) rather than a derived view.  The
collector reads stored rows; it does NOT recompute against current
transactions.  Pinned guarantees:

  1. Collector returns rows that exist in ``generated_rewards``
     for the requested market_day, period.  No filtering on
     feature-flag state — disabling the flag does NOT wipe the
     historical record.
  2. Pre-feature transactions never appear (no rows for them).
  3. Voiding / adjusting a transaction does NOT modify or remove
     existing reward rows.
  4. Rule edits / deletions do NOT retro-apply.
  5. Same data is produced for the Reports screen tab and the cloud
     sync sheet (single read source).
  6. Generated Rewards is in REQUIRED_SYNC_TABS so it syncs by
     default per the 2026-04-30 spec.
  7. Disclaimer banner is present on the Reports screen tab.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def gen_rewards_db(tmp_path):
    db_file = str(tmp_path / "gen_rewards.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'Test Market', 100000, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'V1'), (2, 'V2')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1), (1, 2)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) "
        "VALUES (1, 'SNAP', 100.0, 1, 1, NULL)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) "
        "VALUES (2, 'JH Food Bucks', 100.0, 2, 1, 200)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-30', 'Open', 'T')")
    conn.commit()
    yield conn
    close_connection()


def _seed_order_with_snap(conn, *, order_id=10, customer='C-1',
                           t1_status='Confirmed', t2_status='Confirmed',
                           t1_charged=500, t2_charged=500):
    """One order with two SNAP transactions on the seeded market day."""
    conn.execute(
        "INSERT INTO customer_orders (id, market_day_id, "
        " customer_label, status) VALUES (?, ?, ?, 'Confirmed')",
        (order_id, 1, customer))
    for tid_off, vid, status, charge in [
        (1, 1, t1_status, t1_charged),
        (2, 2, t2_status, t2_charged),
    ]:
        tid = order_id * 100 + tid_off
        conn.execute(
            "INSERT INTO transactions (id, fam_transaction_id, "
            " market_day_id, vendor_id, customer_order_id, "
            " receipt_total, status) "
            "VALUES (?, ?, 1, ?, ?, ?, ?)",
            (tid, f'T-{tid}', vid, order_id, charge * 2, status))
        conn.execute(
            "INSERT INTO payment_line_items"
            " (transaction_id, payment_method_id, "
            "  method_name_snapshot, match_percent_snapshot, "
            "  method_amount, customer_charged, match_amount)"
            " VALUES (?, 1, 'SNAP', 100.0, ?, ?, ?)",
            (tid, charge * 2, charge, charge))
    conn.commit()


def _seed_reward_row(conn, *, order_id=10, market_day_id=1,
                     source='SNAP', source_total=1000,
                     threshold=500, reward='JH Food Bucks',
                     reward_unit=200, n_units=2,
                     reward_total=400, generated_by='Volunteer'):
    """Insert one row into generated_rewards.  Mirrors what
    ``record_generated_rewards`` writes at confirmation time."""
    conn.execute(
        "INSERT INTO generated_rewards"
        " (customer_order_id, market_day_id,"
        "  source_method_name_snapshot, source_total_cents,"
        "  threshold_cents, reward_method_name_snapshot,"
        "  reward_unit_cents, n_units, reward_total_cents,"
        "  generated_by)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (order_id, market_day_id, source, source_total,
         threshold, reward, reward_unit, n_units, reward_total,
         generated_by)
    )
    conn.commit()


class TestCollectorReadsStoredSnapshot:
    """Cloud-sync collector ``_collect_generated_rewards`` is now a
    pure read of the ``generated_rewards`` snapshot table — no
    derivation, no filtering on feature flag state."""

    def test_returns_stored_rows(self, gen_rewards_db):
        """Insert a snapshot row, collector returns it."""
        from fam.sync.data_collector import _collect_generated_rewards
        # Seed an order so the JOIN to customer_orders resolves.
        gen_rewards_db.execute(
            "INSERT INTO customer_orders (id, market_day_id, "
            " customer_label, status) VALUES (10, 1, 'C-1', 'Confirmed')")
        gen_rewards_db.commit()
        _seed_reward_row(gen_rewards_db)

        rows = _collect_generated_rewards(gen_rewards_db, 1)
        assert len(rows) == 1
        r = rows[0]
        assert r['Source Method'] == 'SNAP'
        assert r['Reward Method'] == 'JH Food Bucks'
        assert r['Source Total'] == 10.00
        assert r['Threshold'] == 5.00
        assert r['Units Earned'] == 2
        assert r['Reward Total'] == 4.00
        assert r['Customer'] == 'C-1'
        assert r['Market Name'] == 'Test Market'
        assert r['Generated By'] == 'Volunteer'

    def test_empty_when_no_stored_rows(self, gen_rewards_db):
        """No snapshot rows → empty result.  Confirmed transactions
        on the market day do NOT cause retroactive derivation."""
        from fam.sync.data_collector import _collect_generated_rewards
        # Seed transactions that WOULD have qualified under the old
        # derive-on-demand design.
        _seed_order_with_snap(gen_rewards_db)
        # No generated_rewards rows inserted.
        rows = _collect_generated_rewards(gen_rewards_db, 1)
        assert rows == [], (
            "Pre-feature / pre-rule transactions must NOT "
            "retroactively appear in the report — they only "
            "appear if a snapshot row was written at confirmation")

    def test_disabled_feature_does_not_wipe_history(
            self, gen_rewards_db):
        """User pin: disabling the rewards feature must NOT remove
        previously generated rows from the report."""
        from fam.sync.data_collector import _collect_generated_rewards
        from fam.utils.app_settings import set_rewards_enabled
        gen_rewards_db.execute(
            "INSERT INTO customer_orders (id, market_day_id, "
            " customer_label, status) VALUES (10, 1, 'C-1', 'Confirmed')")
        gen_rewards_db.commit()
        _seed_reward_row(gen_rewards_db)

        # Disable the feature AFTER the row was generated.
        set_rewards_enabled(False)
        rows = _collect_generated_rewards(gen_rewards_db, 1)
        assert len(rows) == 1, (
            "Disabling the rewards feature must NOT remove rows "
            "from the report — the historical record persists")

    def test_voided_txn_does_not_wipe_history(
            self, gen_rewards_db):
        """Pin: voiding a transaction does NOT modify reward rows.
        The cashier already handed the tokens at confirmation
        time."""
        from fam.sync.data_collector import _collect_generated_rewards
        from fam.models.transaction import void_transaction
        _seed_order_with_snap(gen_rewards_db, order_id=10)
        _seed_reward_row(gen_rewards_db, order_id=10,
                         source_total=1000, n_units=2,
                         reward_total=400)
        # Void one of the transactions on the order.
        void_transaction(1001, voided_by='Test')
        # Reward row still present, unchanged.
        rows = _collect_generated_rewards(gen_rewards_db, 1)
        assert len(rows) == 1
        assert rows[0]['Reward Total'] == 4.00, (
            "Reward total must NOT recompute on void — the "
            "snapshot is the historical record")

    def test_rule_deletion_does_not_wipe_history(
            self, gen_rewards_db):
        """Pin: deleting a rule does NOT remove existing reward rows
        — the rule_id is nullable on generated_rewards and the
        snapshot columns capture the rule state at write time."""
        from fam.sync.data_collector import _collect_generated_rewards
        from fam.models.reward_rule import (
            create_reward_rule, delete_reward_rule,
        )
        gen_rewards_db.execute(
            "INSERT INTO customer_orders (id, market_day_id, "
            " customer_label, status) VALUES (10, 1, 'C-1', 'Confirmed')")
        gen_rewards_db.commit()
        rid = create_reward_rule(
            source_method_id=1, threshold_cents=500,
            reward_method_id=2, reward_unit_cents=200)
        # Write a snapshot referencing this rule.
        gen_rewards_db.execute(
            "INSERT INTO generated_rewards"
            " (customer_order_id, market_day_id, rule_id,"
            "  source_method_name_snapshot, source_total_cents,"
            "  threshold_cents, reward_method_name_snapshot,"
            "  reward_unit_cents, n_units, reward_total_cents)"
            " VALUES (10, 1, ?, 'SNAP', 1000, 500,"
            "         'JH Food Bucks', 200, 2, 400)",
            (rid,))
        gen_rewards_db.commit()
        # Delete the rule.
        delete_reward_rule(rid)
        # Reward row still present.
        rows = _collect_generated_rewards(gen_rewards_db, 1)
        assert len(rows) == 1, (
            "Deleting a reward rule must NOT remove existing "
            "snapshot rows — the snapshot stands alone")
        assert rows[0]['Source Method'] == 'SNAP'
        assert rows[0]['Reward Method'] == 'JH Food Bucks'


class TestSyncTabRegistration:

    def test_generated_rewards_in_required_tabs(self):
        """The user pinned: 'Generated Rewards should be a new
        report to sync by default.'  REQUIRED_SYNC_TABS membership
        is the implementation."""
        from fam.utils.app_settings import REQUIRED_SYNC_TABS
        assert 'Generated Rewards' in REQUIRED_SYNC_TABS

    def test_generated_rewards_sync_enabled_by_default(self):
        from fam.utils.app_settings import is_sync_tab_enabled
        assert is_sync_tab_enabled('Generated Rewards') is True


class TestReportsScreenTab:
    """Reports screen → Generated Rewards tab."""

    def test_tab_registered(self, qtbot, gen_rewards_db):
        from fam.ui.reports_screen import ReportsScreen
        screen = ReportsScreen()
        qtbot.addWidget(screen)
        labels = [screen.tabs.tabText(i)
                  for i in range(screen.tabs.count())]
        assert 'Generated Rewards' in labels

    def test_tab_table_populated_from_stored_rows(
            self, qtbot, gen_rewards_db):
        """End-to-end: seed snapshot row → refresh → table has it.

        Note: under the new write-once contract, transactions on
        the market day do NOT cause retroactive derivation; only
        rows actually written into ``generated_rewards`` appear.
        """
        from fam.ui.reports_screen import ReportsScreen
        gen_rewards_db.execute(
            "INSERT INTO customer_orders (id, market_day_id, "
            " customer_label, status) VALUES (10, 1, 'C-1', 'Confirmed')")
        gen_rewards_db.commit()
        _seed_reward_row(gen_rewards_db)

        screen = ReportsScreen()
        qtbot.addWidget(screen)
        screen.refresh()
        assert screen.rewards_table.rowCount() == 1
        # Customer column = 2 (unchanged)
        assert screen.rewards_table.item(0, 2).text() == 'C-1'
        # Reward Total column shifted from 9 to 10 in v2.0.6 when
        # Zip Code was inserted at column 3 (between Customer and
        # Source Method).  See test_zip_code_in_reports.py for the
        # column-layout pin.
        assert '$4.00' in screen.rewards_table.item(0, 10).text()
        assert len(screen._rewards_data) == 1

    def test_tab_empty_for_pre_feature_transactions(
            self, qtbot, gen_rewards_db):
        """The bug the user reported: pre-feature transactions
        must NOT retroactively populate the report when a rule is
        added later."""
        from fam.ui.reports_screen import ReportsScreen
        from fam.utils.app_settings import set_rewards_enabled
        from fam.models.reward_rule import create_reward_rule

        # Pre-feature: customer paid with SNAP, no reward row
        # was written at confirmation (feature was off).
        _seed_order_with_snap(gen_rewards_db)
        # Now coordinator turns on the feature + adds a rule.
        set_rewards_enabled(True)
        create_reward_rule(
            source_method_id=1, threshold_cents=500,
            reward_method_id=2, reward_unit_cents=200)

        screen = ReportsScreen()
        qtbot.addWidget(screen)
        screen.refresh()
        # No retro rows — the report stays empty until a NEW
        # confirmation writes a snapshot.
        assert screen.rewards_table.rowCount() == 0, (
            "Bug fix: enabling the feature + adding a rule must "
            "NOT retroactively show rewards for pre-feature "
            "transactions in the Generated Rewards report")


class TestExportGeneratedRewards:

    def test_export_csv_contains_header_and_row(
            self, gen_rewards_db, tmp_path):
        from fam.utils.export import export_generated_rewards
        rows = [{
            'Market Name': 'M',
            'Date': '2026-04-30',
            'Customer': 'C-1',
            'Source Method': 'SNAP',
            'Source Total': 5.00,
            'Threshold': 5.00,
            'Reward Method': 'JH Food Bucks',
            'Reward Unit': 2.00,
            'Units Earned': 1,
            'Reward Total': 2.00,
        }]
        out = str(tmp_path / "rewards.csv")
        export_generated_rewards(rows, out)
        with open(out, encoding='utf-8') as f:
            text = f.read()
        assert 'Market Name' in text
        assert 'JH Food Bucks' in text
        assert '5.0' in text or '5.00' in text


class TestNoFinancialPipelineImpact:
    """Source-level guards: rewards must NOT touch financial code."""

    def test_no_reward_columns_added_to_payment_line_items(
            self, gen_rewards_db):
        cols = [c[1] for c in gen_rewards_db.execute(
            "PRAGMA table_info(payment_line_items)").fetchall()]
        for forbidden in ('reward_amount', 'reward_method_id',
                           'reward_units', 'rewards'):
            assert forbidden not in cols, (
                f"payment_line_items must not carry rewards data: "
                f"{cols}")

    def test_no_reward_columns_added_to_transactions(
            self, gen_rewards_db):
        cols = [c[1] for c in gen_rewards_db.execute(
            "PRAGMA table_info(transactions)").fetchall()]
        for forbidden in ('reward_amount', 'reward_method_id',
                           'reward_units', 'rewards'):
            assert forbidden not in cols
