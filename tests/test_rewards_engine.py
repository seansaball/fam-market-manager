"""Tests for the rewards program — pure derivation logic + model
+ schema migration + seed.

The rewards program is a customer-facing marketing/loyalty add-on
that runs entirely outside the financial pipeline (vendor
reimbursement, FAM match, the per-line invariant — none affected).
Pinned guarantees:

  1. Math is whole-increment, NOT pro-rated.
  2. Source totals are summed PER ORDER (multi-vendor sum), filtered
     to the rule's source method, restricted to non-voided
     transactions.
  3. Reward methods MUST be denominated (model-layer guard).
  4. Source and reward must differ (schema-level CHECK).
  5. Voided transactions don't contribute to source totals →
     report recomputes against current state.
  6. Adjusting a transaction down also recomputes (lower source
     total → fewer reward units).
  7. Default seed: SNAP × $5 → $2 × JH Food Bucks, ACTIVE.
"""

import sqlite3
import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def rewards_db(tmp_path):
    db_file = str(tmp_path / "rewards.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'V1')")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (2, 'V2')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1), (1, 2)")
    # SNAP id=1 (non-denom), JH Food Bucks id=2 ($2 denom),
    # Cash id=3 (non-denom).
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) "
        "VALUES (1, 'SNAP', 100.0, 1, 1, NULL)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) "
        "VALUES (2, 'JH Food Bucks', 100.0, 2, 1, 200)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) "
        "VALUES (3, 'Cash', 0.0, 3, 1, NULL)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES (1, 1, '2026-04-30', 'Open', 'T')")
    conn.commit()
    yield conn
    close_connection()


# ──────────────────────────────────────────────────────────────────
# 1.  Pure math — compute_reward_for_rule
# ──────────────────────────────────────────────────────────────────

class TestComputeRewardForRule:
    """Whole-increment math.  No floats, no pro-rating."""

    def test_exact_threshold_yields_one_unit(self):
        from fam.utils.rewards import compute_reward_for_rule
        n, total = compute_reward_for_rule(500, 500, 200)
        assert n == 1
        assert total == 200

    def test_above_threshold_below_two_yields_one(self):
        """$7 SNAP under $5 threshold → 1 unit (NOT 1.4)."""
        from fam.utils.rewards import compute_reward_for_rule
        n, total = compute_reward_for_rule(700, 500, 200)
        assert n == 1
        assert total == 200

    def test_two_full_thresholds_yields_two(self):
        from fam.utils.rewards import compute_reward_for_rule
        n, total = compute_reward_for_rule(1000, 500, 200)
        assert n == 2
        assert total == 400

    def test_below_threshold_yields_zero(self):
        from fam.utils.rewards import compute_reward_for_rule
        n, total = compute_reward_for_rule(499, 500, 200)
        assert n == 0
        assert total == 0

    def test_zero_source_yields_zero(self):
        from fam.utils.rewards import compute_reward_for_rule
        assert compute_reward_for_rule(0, 500, 200) == (0, 0)

    def test_negative_source_clamps_to_zero(self):
        """Defensive: shouldn't happen, but if it does, yield zero
        rather than crash on a divmod with negative numerator."""
        from fam.utils.rewards import compute_reward_for_rule
        assert compute_reward_for_rule(-100, 500, 200) == (0, 0)

    def test_zero_threshold_returns_zero(self):
        """Schema CHECK forbids threshold=0 but the helper must not
        ZeroDivisionError if someone passes a corrupt rule."""
        from fam.utils.rewards import compute_reward_for_rule
        assert compute_reward_for_rule(1000, 0, 200) == (0, 0)


# ──────────────────────────────────────────────────────────────────
# 2.  Pure math — compute_rewards_for_order with multiple rules
# ──────────────────────────────────────────────────────────────────

class TestComputeRewardsForOrder:

    METHOD_LOOKUP = {
        1: {'id': 1, 'name': 'SNAP', 'denomination': None},
        2: {'id': 2, 'name': 'JH Food Bucks', 'denomination': 200},
        3: {'id': 3, 'name': 'Cash', 'denomination': None},
        4: {'id': 4, 'name': 'Food RX', 'denomination': 1000},
    }

    def test_single_rule_single_method(self):
        from fam.utils.rewards import compute_rewards_for_order
        rules = [{'id': 10, 'source_method_id': 1,
                  'threshold_cents': 500, 'reward_method_id': 2,
                  'reward_unit_cents': 200}]
        out = compute_rewards_for_order(
            order_source_totals_by_method={1: 700},
            active_rules=rules,
            method_lookup=self.METHOD_LOOKUP,
        )
        assert len(out) == 1
        line = out[0]
        assert line.source_method_name == 'SNAP'
        assert line.reward_method_name == 'JH Food Bucks'
        assert line.n_units == 1
        assert line.reward_total_cents == 200
        assert line.source_total_cents == 700

    def test_multiple_rules_independent(self):
        """Two active rules — SNAP→FB and Cash→Food RX — both fire
        when their sources cross the threshold."""
        from fam.utils.rewards import compute_rewards_for_order
        rules = [
            {'id': 10, 'source_method_id': 1, 'threshold_cents': 500,
             'reward_method_id': 2, 'reward_unit_cents': 200},
            {'id': 11, 'source_method_id': 3, 'threshold_cents': 1000,
             'reward_method_id': 4, 'reward_unit_cents': 1000},
        ]
        out = compute_rewards_for_order(
            order_source_totals_by_method={1: 1000, 3: 2000},
            active_rules=rules,
            method_lookup=self.METHOD_LOOKUP,
        )
        # SNAP: $10 / $5 → 2 units of $2 = $4 of FB
        # Cash: $20 / $10 → 2 units of $10 = $20 of Food RX
        assert len(out) == 2
        snap_line = next(l for l in out if l.source_method_name == 'SNAP')
        cash_line = next(l for l in out if l.source_method_name == 'Cash')
        assert snap_line.reward_total_cents == 400
        assert cash_line.reward_total_cents == 2000

    def test_rule_below_threshold_dropped(self):
        """Rule whose source total is below threshold yields nothing
        and is excluded from the result list (don't show $0 lines)."""
        from fam.utils.rewards import compute_rewards_for_order
        rules = [{'id': 10, 'source_method_id': 1,
                  'threshold_cents': 500, 'reward_method_id': 2,
                  'reward_unit_cents': 200}]
        out = compute_rewards_for_order(
            order_source_totals_by_method={1: 300},  # below $5
            active_rules=rules,
            method_lookup=self.METHOD_LOOKUP,
        )
        assert out == []

    def test_no_source_for_rule_method(self):
        """Rule's source method has zero customer_charged on the
        order → drop the rule from output."""
        from fam.utils.rewards import compute_rewards_for_order
        rules = [{'id': 10, 'source_method_id': 1,
                  'threshold_cents': 500, 'reward_method_id': 2,
                  'reward_unit_cents': 200}]
        out = compute_rewards_for_order(
            order_source_totals_by_method={3: 5000},  # only Cash
            active_rules=rules,
            method_lookup=self.METHOD_LOOKUP,
        )
        assert out == []


# ──────────────────────────────────────────────────────────────────
# 3.  Schema + seed
# ──────────────────────────────────────────────────────────────────

class TestSchemaAndSeed:

    def test_schema_version_is_at_least_30(self, rewards_db):
        """v30 added the generated_rewards snapshot table.

        v1.9.10 follow-up (2026-05-01): pinned by ``>= 30`` rather
        than equality, since v31 adds defense-in-depth triggers
        (PLI non-negativity UPDATE, Voided one-way) without
        affecting this table's contract.
        """
        v = rewards_db.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        assert v >= 30

    def test_reward_rules_table_exists(self, rewards_db):
        cols = rewards_db.execute(
            "PRAGMA table_info(reward_rules)"
        ).fetchall()
        names = {c[1] for c in cols}
        assert names >= {
            'id', 'source_method_id', 'threshold_cents',
            'reward_method_id', 'reward_unit_cents', 'is_active',
            'created_at', 'updated_at',
        }

    def test_check_threshold_positive(self, rewards_db):
        with pytest.raises(sqlite3.IntegrityError):
            rewards_db.execute(
                "INSERT INTO reward_rules"
                " (source_method_id, threshold_cents, reward_method_id,"
                "  reward_unit_cents) VALUES (1, 0, 2, 200)")

    def test_check_reward_unit_positive(self, rewards_db):
        with pytest.raises(sqlite3.IntegrityError):
            rewards_db.execute(
                "INSERT INTO reward_rules"
                " (source_method_id, threshold_cents, reward_method_id,"
                "  reward_unit_cents) VALUES (1, 500, 2, 0)")

    def test_check_source_neq_reward(self, rewards_db):
        with pytest.raises(sqlite3.IntegrityError):
            rewards_db.execute(
                "INSERT INTO reward_rules"
                " (source_method_id, threshold_cents, reward_method_id,"
                "  reward_unit_cents) VALUES (1, 500, 1, 200)")

    def test_seed_inserts_default_rule(self, tmp_path):
        """Fresh seed run inserts SNAP × $5 → $2 × FB rule, active."""
        db_file = str(tmp_path / "seed_default.db")
        close_connection()
        set_db_path(db_file)
        initialize_database()
        conn = get_connection()
        # initialize_database may have left payment methods seeded
        # (from migrations).  Run the full seed to populate the rule.
        from fam.database.seed import seed_sample_data
        # seed_sample_data() bails out if markets exist; ensure we
        # are in a fresh state.
        conn.execute("DELETE FROM reward_rules")
        conn.execute("DELETE FROM payment_methods")
        conn.execute("DELETE FROM markets")
        conn.commit()
        seed_sample_data()
        rules = conn.execute(
            "SELECT * FROM reward_rules WHERE is_active = 1"
        ).fetchall()
        assert len(rules) == 1, (
            f"Expected exactly one default rule, got {len(rules)}")
        rule = dict(rules[0])
        # Look up SNAP and JH Food Bucks IDs.
        snap_id = conn.execute(
            "SELECT id FROM payment_methods WHERE name='SNAP'"
        ).fetchone()[0]
        fb_id = conn.execute(
            "SELECT id FROM payment_methods WHERE name='JH Food Bucks'"
        ).fetchone()[0]
        assert rule['source_method_id'] == snap_id
        assert rule['reward_method_id'] == fb_id
        assert rule['threshold_cents'] == 500
        assert rule['reward_unit_cents'] == 200
        close_connection()


# ──────────────────────────────────────────────────────────────────
# 4.  Model layer — CRUD + denominated guard
# ──────────────────────────────────────────────────────────────────

class TestRewardRuleModel:

    def test_create_and_list(self, rewards_db):
        from fam.models.reward_rule import (
            create_reward_rule, get_all_reward_rules,
            get_active_reward_rules,
        )
        rid = create_reward_rule(
            source_method_id=1, threshold_cents=500,
            reward_method_id=2, reward_unit_cents=200)
        all_rules = get_all_reward_rules()
        assert any(r['id'] == rid for r in all_rules)
        active = get_active_reward_rules()
        assert any(r['id'] == rid for r in active)

    def test_create_rejects_non_denominated_reward(self, rewards_db):
        """Cash (no denomination) cannot be a reward method —
        the rep can't physically hand out arbitrary cash amounts."""
        from fam.models.reward_rule import create_reward_rule
        with pytest.raises(ValueError, match="denominated"):
            create_reward_rule(
                source_method_id=1, threshold_cents=500,
                reward_method_id=3,  # Cash, NULL denomination
                reward_unit_cents=200)

    def test_update_toggle_active(self, rewards_db):
        from fam.models.reward_rule import (
            create_reward_rule, update_reward_rule,
            get_active_reward_rules, get_reward_rule_by_id,
        )
        rid = create_reward_rule(
            source_method_id=1, threshold_cents=500,
            reward_method_id=2, reward_unit_cents=200)
        update_reward_rule(rid, is_active=0)
        assert get_reward_rule_by_id(rid)['is_active'] == 0
        # Disabled rules don't appear in the active list.
        assert not any(
            r['id'] == rid for r in get_active_reward_rules())

    def test_delete(self, rewards_db):
        from fam.models.reward_rule import (
            create_reward_rule, delete_reward_rule,
            get_reward_rule_by_id,
        )
        rid = create_reward_rule(
            source_method_id=1, threshold_cents=500,
            reward_method_id=2, reward_unit_cents=200)
        delete_reward_rule(rid)
        assert get_reward_rule_by_id(rid) is None


# ──────────────────────────────────────────────────────────────────
# 5.  Order-level source-total query — multi-vendor + voids
# ──────────────────────────────────────────────────────────────────

class TestOrderSourceTotals:

    def _seed_two_vendor_order(self, conn, *, void_t2=False,
                                adjusted_t2=False):
        """Customer order with two vendor receipts, both paid in
        SNAP (50% from each vendor's receipt).  Optionally void or
        adjust the second one."""
        conn.execute(
            "INSERT INTO customer_orders (id, market_day_id, "
            " customer_label, status) VALUES (?, ?, ?, ?)",
            (10, 1, 'C-RW', 'Confirmed'))
        # Vendor 1 receipt: $5 SNAP customer + $5 match = $10 method
        conn.execute(
            "INSERT INTO transactions (id, fam_transaction_id, "
            " market_day_id, vendor_id, customer_order_id, "
            " receipt_total, status) VALUES (101, 'T1', 1, 1, 10, "
            "  1000, 'Confirmed')")
        conn.execute(
            "INSERT INTO payment_line_items"
            " (transaction_id, payment_method_id, "
            "  method_name_snapshot, match_percent_snapshot, "
            "  method_amount, customer_charged, match_amount)"
            " VALUES (101, 1, 'SNAP', 100.0, 1000, 500, 500)")
        # Vendor 2 receipt: $5 SNAP customer + $5 match = $10 method
        t2_status = ('Voided' if void_t2 else
                     'Adjusted' if adjusted_t2 else 'Confirmed')
        conn.execute(
            "INSERT INTO transactions (id, fam_transaction_id, "
            " market_day_id, vendor_id, customer_order_id, "
            " receipt_total, status) VALUES (102, 'T2', 1, 2, 10, "
            "  1000, ?)", (t2_status,))
        conn.execute(
            "INSERT INTO payment_line_items"
            " (transaction_id, payment_method_id, "
            "  method_name_snapshot, match_percent_snapshot, "
            "  method_amount, customer_charged, match_amount)"
            " VALUES (102, 1, 'SNAP', 100.0, 1000, 500, 500)")
        conn.commit()

    def test_sum_across_two_vendors(self, rewards_db):
        from fam.models.reward_rule import (
            get_order_source_totals_by_method,
        )
        self._seed_two_vendor_order(rewards_db)
        totals = get_order_source_totals_by_method(10)
        # SNAP id=1 → $5 + $5 = $10 = 1000 cents.
        assert totals == {1: 1000}

    def test_voided_txn_excluded(self, rewards_db):
        from fam.models.reward_rule import (
            get_order_source_totals_by_method,
        )
        self._seed_two_vendor_order(rewards_db, void_t2=True)
        totals = get_order_source_totals_by_method(10)
        # Only V1's $5 contributes.
        assert totals == {1: 500}

    def test_adjusted_txn_included(self, rewards_db):
        """'Adjusted' status is counted (only 'Voided' is excluded)."""
        from fam.models.reward_rule import (
            get_order_source_totals_by_method,
        )
        self._seed_two_vendor_order(rewards_db, adjusted_t2=True)
        totals = get_order_source_totals_by_method(10)
        assert totals == {1: 1000}


# ──────────────────────────────────────────────────────────────────
# 6.  End-to-end: rules + DB query → reward lines
# ──────────────────────────────────────────────────────────────────

class TestEndToEndOrderRewards:

    def test_two_vendor_order_yields_one_fb_reward(self, rewards_db):
        """The user's canonical scenario: customer pays $5 SNAP at
        one vendor + $0 SNAP at another (or vice versa), order total
        SNAP = $5 → exactly 1 × $2 FB token."""
        from fam.models.reward_rule import (
            create_reward_rule, get_active_reward_rules,
            get_order_source_totals_by_method, get_method_lookup,
        )
        from fam.utils.rewards import compute_rewards_for_order

        # Only one $5-SNAP receipt on the order.
        rewards_db.execute(
            "INSERT INTO customer_orders (id, market_day_id, "
            " customer_label, status) VALUES (50, 1, 'C-1', 'Confirmed')")
        rewards_db.execute(
            "INSERT INTO transactions (id, fam_transaction_id, "
            " market_day_id, vendor_id, customer_order_id, "
            " receipt_total, status) VALUES (501, 'T-501', 1, 1, 50, "
            "  1000, 'Confirmed')")
        rewards_db.execute(
            "INSERT INTO payment_line_items"
            " (transaction_id, payment_method_id, "
            "  method_name_snapshot, match_percent_snapshot, "
            "  method_amount, customer_charged, match_amount)"
            " VALUES (501, 1, 'SNAP', 100.0, 1000, 500, 500)")
        rewards_db.commit()

        create_reward_rule(
            source_method_id=1, threshold_cents=500,
            reward_method_id=2, reward_unit_cents=200)

        totals = get_order_source_totals_by_method(50)
        rules = get_active_reward_rules()
        lookup = get_method_lookup()
        lines = compute_rewards_for_order(totals, rules, lookup)

        assert len(lines) == 1
        line = lines[0]
        assert line.source_method_name == 'SNAP'
        assert line.reward_method_name == 'JH Food Bucks'
        assert line.n_units == 1
        assert line.reward_total_cents == 200, (
            "Customer paid $5 SNAP → exactly $2 in FB tokens — "
            "the user's canonical 2026-04-30 specification.")
