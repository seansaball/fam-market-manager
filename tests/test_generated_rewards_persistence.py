"""Tests for the rewards write-once snapshot contract.

User-reported (2026-04-30 follow-up):

    "When I applied the rule it looks like it retroactively
     applied it to previous transactions and added them to the
     Generated Rewards page, we don't want that to happen, it
     should only apply to net-new transactions and if it
     becomes disabled, anything that was generated should
     persist in the reports and not be wiped out either."

Pinned guarantees of the new write-once design (schema v30):

  1. ``record_generated_rewards`` writes once at confirmation,
     never modifies existing rows.
  2. The snapshot captures rule state at write time — later
     edits / deletions of the rule do NOT affect existing rows.
  3. Voided / adjusted transactions do NOT modify reward rows.
  4. Re-firing the confirmation flow on the same order is a
     no-op (idempotent) — never duplicates rows.
  5. The rewards engine's pre-confirmation derivation
     (``compute_rewards_for_order``) is still used to compute
     what to write, but the WRITE is the source of truth from
     that moment on.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def persist_db(tmp_path):
    db_file = str(tmp_path / "persist.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-30', 'Open', 'Vol')")
    conn.execute(
        "INSERT INTO customer_orders (id, market_day_id, "
        " customer_label, status) "
        "VALUES (10, 1, 'C-RW', 'Confirmed')")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) "
        "VALUES (1, 'SNAP', 100.0, 1, 1, NULL)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) "
        "VALUES (2, 'JH Food Bucks', 100.0, 2, 1, 200)")
    conn.commit()
    yield conn
    close_connection()


def _make_reward_line(*, source_total=1000, n_units=2,
                      reward_total=400):
    from fam.utils.rewards import RewardLine
    return RewardLine(
        rule_id=99,
        source_method_id=1,
        source_method_name='SNAP',
        source_total_cents=source_total,
        threshold_cents=500,
        reward_method_id=2,
        reward_method_name='JH Food Bucks',
        reward_unit_cents=200,
        n_units=n_units,
        reward_total_cents=reward_total,
    )


class TestRecordGeneratedRewards:

    def test_writes_one_row_per_reward_line(self, persist_db):
        from fam.models.generated_reward import (
            record_generated_rewards,
        )
        n = record_generated_rewards(
            customer_order_id=10,
            market_day_id=1,
            reward_lines=[_make_reward_line()],
            generated_by='Vol',
        )
        assert n == 1
        row = persist_db.execute(
            "SELECT * FROM generated_rewards"
        ).fetchone()
        assert row['customer_order_id'] == 10
        assert row['source_method_name_snapshot'] == 'SNAP'
        assert row['source_total_cents'] == 1000
        assert row['n_units'] == 2
        assert row['reward_total_cents'] == 400
        assert row['generated_by'] == 'Vol'

    def test_idempotent_on_reentrant_call(self, persist_db):
        """Calling twice for the same order is a no-op on the
        second call — historical record is preserved."""
        from fam.models.generated_reward import (
            record_generated_rewards,
        )
        record_generated_rewards(
            customer_order_id=10, market_day_id=1,
            reward_lines=[_make_reward_line()],
            generated_by='Vol')
        # Second call → no-op.
        n2 = record_generated_rewards(
            customer_order_id=10, market_day_id=1,
            reward_lines=[_make_reward_line(reward_total=999)],
            generated_by='Vol')
        assert n2 == 0
        # Only one row in the table.
        count = persist_db.execute(
            "SELECT COUNT(*) FROM generated_rewards"
        ).fetchone()[0]
        assert count == 1
        # Original snapshot intact.
        row = persist_db.execute(
            "SELECT reward_total_cents FROM generated_rewards"
        ).fetchone()
        assert row['reward_total_cents'] == 400

    def test_empty_reward_lines_writes_nothing(self, persist_db):
        from fam.models.generated_reward import (
            record_generated_rewards,
        )
        n = record_generated_rewards(
            customer_order_id=10, market_day_id=1,
            reward_lines=[], generated_by='Vol')
        assert n == 0
        count = persist_db.execute(
            "SELECT COUNT(*) FROM generated_rewards"
        ).fetchone()[0]
        assert count == 0


class TestSnapshotIsImmutable:

    def test_rule_deletion_preserves_snapshot(self, persist_db):
        """Delete the rule — snapshot row still has the captured
        threshold/reward_unit etc."""
        from fam.models.generated_reward import (
            record_generated_rewards, get_generated_rewards_for_order,
        )
        from fam.models.reward_rule import (
            create_reward_rule, delete_reward_rule,
        )
        rid = create_reward_rule(
            source_method_id=1, threshold_cents=500,
            reward_method_id=2, reward_unit_cents=200)
        rl = _make_reward_line()
        # Replace rule_id with the real one for snapshot integrity.
        rl = rl._replace(rule_id=rid)
        record_generated_rewards(
            customer_order_id=10, market_day_id=1,
            reward_lines=[rl], generated_by='Vol')
        delete_reward_rule(rid)
        rows = get_generated_rewards_for_order(10)
        assert len(rows) == 1
        assert rows[0]['threshold_cents'] == 500
        assert rows[0]['reward_unit_cents'] == 200
        assert rows[0]['source_method_name_snapshot'] == 'SNAP'
        assert rows[0]['reward_method_name_snapshot'] == 'JH Food Bucks'

    def test_rule_edit_does_not_modify_existing_snapshots(
            self, persist_db):
        from fam.models.generated_reward import (
            record_generated_rewards, get_generated_rewards_for_order,
        )
        from fam.models.reward_rule import (
            create_reward_rule, update_reward_rule,
        )
        rid = create_reward_rule(
            source_method_id=1, threshold_cents=500,
            reward_method_id=2, reward_unit_cents=200)
        rl = _make_reward_line()._replace(rule_id=rid)
        record_generated_rewards(
            customer_order_id=10, market_day_id=1,
            reward_lines=[rl], generated_by='Vol')
        # Coordinator changes the rule to something more generous.
        update_reward_rule(
            rid, threshold_cents=300, reward_unit_cents=500)
        rows = get_generated_rewards_for_order(10)
        # Existing snapshot still shows the OLD rule values.
        assert rows[0]['threshold_cents'] == 500
        assert rows[0]['reward_unit_cents'] == 200

    def test_void_does_not_modify_snapshot(self, persist_db):
        """Voiding a transaction does NOT modify or delete the
        reward snapshot (the cashier already handed the tokens)."""
        from fam.models.generated_reward import (
            record_generated_rewards, get_generated_rewards_for_order,
        )
        from fam.models.transaction import (
            create_transaction, void_transaction,
        )
        # Need a vendor before creating the transaction (NOT NULL
        # constraint on transactions.vendor_id).
        persist_db.execute(
            "INSERT INTO vendors (id, name) VALUES (1, 'V1')")
        persist_db.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, 1)")
        persist_db.commit()
        tid, _ = create_transaction(
            market_day_id=1, vendor_id=1,
            customer_order_id=10, receipt_total=1000,
            market_day_date='2026-04-30')

        record_generated_rewards(
            customer_order_id=10, market_day_id=1,
            reward_lines=[_make_reward_line()],
            generated_by='Vol')
        # Void the txn.
        void_transaction(tid, voided_by='Test')
        rows = get_generated_rewards_for_order(10)
        # Snapshot intact.
        assert len(rows) == 1
        assert rows[0]['reward_total_cents'] == 400


class TestPreFeatureTransactionsHaveNoSnapshot:
    """The user's bug: confirming transactions BEFORE the rewards
    feature was on (or before any rule existed) must produce ZERO
    snapshot rows.  Enabling the feature later does not retroactively
    create them."""

    def test_no_rows_for_pre_feature_orders(self, persist_db):
        from fam.models.generated_reward import (
            get_generated_rewards_for_market_day,
        )
        # No record_generated_rewards call has been made — the
        # only way for rows to exist is via that call at confirmation.
        rows = get_generated_rewards_for_market_day(1)
        assert rows == []

    def test_enabling_feature_after_no_retro_application(
            self, persist_db):
        """Even after enabling the feature + creating a rule,
        the report must remain empty until a NEW order is
        confirmed."""
        from fam.utils.app_settings import set_rewards_enabled
        from fam.models.reward_rule import create_reward_rule
        from fam.models.generated_reward import (
            get_generated_rewards_for_market_day,
        )
        set_rewards_enabled(True)
        create_reward_rule(
            source_method_id=1, threshold_cents=500,
            reward_method_id=2, reward_unit_cents=200)
        # Still empty — no confirmation event has fired since
        # the feature was enabled.
        rows = get_generated_rewards_for_market_day(1)
        assert rows == []


class TestDisablingFeaturePreservesHistory:
    """User pin: disabling the rewards feature must NOT wipe rows
    from the report."""

    def test_disable_keeps_existing_snapshot_rows(self, persist_db):
        from fam.utils.app_settings import set_rewards_enabled
        from fam.models.generated_reward import (
            record_generated_rewards, get_generated_rewards_for_market_day,
        )
        set_rewards_enabled(True)
        record_generated_rewards(
            customer_order_id=10, market_day_id=1,
            reward_lines=[_make_reward_line()],
            generated_by='Vol')
        # Now disable.
        set_rewards_enabled(False)
        rows = get_generated_rewards_for_market_day(1)
        assert len(rows) == 1, (
            "Disabling the rewards feature must not remove rows "
            "from the historical record")
        assert rows[0]['reward_total_cents'] == 400
