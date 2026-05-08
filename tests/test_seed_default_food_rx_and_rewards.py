"""Pin two seed-data invariants the user asked to lock in (2026-05-06):

  1. **Food RX is denominated at $10 increments** in the default seed.
     Auto-Distribute and per-row binding both depend on the
     ``denomination`` column being non-NULL for paper-check methods;
     a Food RX seeded as non-denominated would let volunteers enter
     fractional amounts (e.g. $7.83) and break the per-vendor binding
     guarantees added in schema v24.

  2. **Default reward rule is auto-inserted** on Load Defaults — the
     classic "$5 SNAP → 1 × $2 JH Food Bucks" loyalty rule.  Active
     by default; coordinators disable via Settings → Rewards if the
     market doesn't run the program.  Without this, fresh-install
     coordinators have to manually click "Add Rule" with the form's
     pre-populated values, which feels like an unnecessary step.

These tests run against the canonical ``seed_sample_data`` path
(invoked by ``MainWindow._auto_configure`` when the user clicks
"Yes — Load Default Data" at the end of the tutorial).
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_seed_defaults.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield get_connection()
    close_connection()


def _run_seed(conn):
    """Run seed_sample_data and assert it succeeded.  The seed early-
    returns False when markets already exist, which would mask any
    real failure — assert success explicitly."""
    from fam.database.seed import seed_sample_data
    # initialize_database() may have placed seed-time rows (e.g. UF
    # at id 9999, FMNP via v8 migration).  seed_sample_data only
    # short-circuits on populated MARKETS — payment_methods leftovers
    # are fine.  But to test the canonical seed path against a fully-
    # empty schema we wipe payment_methods first to avoid UNIQUE
    # collisions.
    conn.execute("DELETE FROM market_payment_methods")
    conn.execute("DELETE FROM vendor_payment_methods")
    conn.execute("DELETE FROM reward_rules")
    conn.execute("DELETE FROM payment_methods")
    conn.commit()
    ok = seed_sample_data()
    assert ok, "seed_sample_data must succeed on an empty DB"


# ──────────────────────────────────────────────────────────────────
# 1. Food RX is denominated at $10
# ──────────────────────────────────────────────────────────────────


class TestFoodRxDenomination:
    """The Food RX paper-check method must be seeded with a
    $10 (1000-cent) denomination.  Non-denominated would let
    volunteers enter $7.83 against a Food RX row, which breaks
    the per-vendor binding model and inflates FAM match relative
    to the physical paper checks the program actually issues."""

    def test_food_rx_seeded_with_ten_dollar_denomination(
            self, fresh_db):
        _run_seed(fresh_db)

        row = fresh_db.execute(
            "SELECT name, match_percent, denomination, is_active "
            "FROM payment_methods WHERE name = 'Food RX'"
        ).fetchone()
        assert row is not None, (
            "Food RX must be present in the default seed.")
        assert row['denomination'] == 1000, (
            f"Food RX must be seeded with denomination=1000 cents "
            f"($10).  Got: {row['denomination']!r}.  Paper-check "
            f"methods are physical instruments — non-denominated "
            f"breaks per-vendor binding and FAM-match math.")
        assert row['match_percent'] == 100.0, (
            "Food RX is a 100%-match paper check program.")
        assert row['is_active'] == 1, (
            "Food RX is active by default — unlike FMNP, the "
            "program is currently in use across FAM markets.")


# ──────────────────────────────────────────────────────────────────
# 2. Default reward rule auto-inserted
# ──────────────────────────────────────────────────────────────────


class TestDefaultRewardRule:
    """The Load Defaults path must auto-insert the canonical rewards
    rule (no "Add Rule" click required)."""

    def test_default_rule_count_is_one(self, fresh_db):
        _run_seed(fresh_db)
        rule_count = fresh_db.execute(
            "SELECT COUNT(*) FROM reward_rules").fetchone()[0]
        assert rule_count == 1, (
            f"Default seed must auto-insert exactly one reward rule "
            f"(the canonical $5 SNAP → 1 × $2 JH Food Bucks).  Got "
            f"{rule_count} rule(s).  Pre-fix coordinators had to "
            f"open Settings → Rewards and click 'Add Rule' even "
            f"though the form pre-populated those exact values.")

    def test_default_rule_is_snap_to_food_bucks_active(
            self, fresh_db):
        _run_seed(fresh_db)
        rule = fresh_db.execute("""
            SELECT rr.threshold_cents, rr.reward_unit_cents,
                   rr.is_active,
                   src.name AS source_name,
                   tgt.name AS target_name
            FROM reward_rules rr
            JOIN payment_methods src
              ON rr.source_method_id = src.id
            JOIN payment_methods tgt
              ON rr.reward_method_id = tgt.id
        """).fetchone()
        assert rule is not None, (
            "Reward rule join failed — source or target method missing.")
        assert rule['source_name'] == 'SNAP', (
            f"Default rule's source method must be SNAP.  Got: "
            f"{rule['source_name']!r}.")
        assert rule['target_name'] == 'JH Food Bucks', (
            f"Default rule's reward method must be JH Food Bucks.  "
            f"Got: {rule['target_name']!r}.")
        assert rule['threshold_cents'] == 500, (
            f"Threshold must be 500 cents ($5).  Got: "
            f"{rule['threshold_cents']}.")
        assert rule['reward_unit_cents'] == 200, (
            f"Reward unit must be 200 cents ($2).  Got: "
            f"{rule['reward_unit_cents']}.")
        assert rule['is_active'] == 1, (
            f"Default rule must be active by default.  Got "
            f"is_active={rule['is_active']}.")

    def test_reward_target_is_denominated(self, fresh_db):
        """The reward method (JH Food Bucks) must be denominated —
        rewards are physical scrip the FAM rep hands to the customer.
        A non-denominated reward method makes 'hand the customer
        N units' meaningless."""
        _run_seed(fresh_db)
        denom = fresh_db.execute(
            "SELECT denomination FROM payment_methods "
            "WHERE name = 'JH Food Bucks'"
        ).fetchone()
        assert denom is not None
        assert denom['denomination'] and denom['denomination'] > 0, (
            "Reward target (JH Food Bucks) must be denominated.")


# ──────────────────────────────────────────────────────────────────
# 3. Cross-check: full seed produces a self-consistent state
# ──────────────────────────────────────────────────────────────────


class TestSeedSelfConsistency:
    """Smoke-tests confirming the Load Defaults output is internally
    coherent — every payment method assigned to every market, every
    vendor permissively eligible for every method, the reward rule
    references valid methods.
    """

    def test_every_payment_method_assigned_to_every_market(
            self, fresh_db):
        _run_seed(fresh_db)
        market_count = fresh_db.execute(
            "SELECT COUNT(*) FROM markets").fetchone()[0]
        method_count = fresh_db.execute(
            "SELECT COUNT(*) FROM payment_methods "
            "WHERE COALESCE(is_system, 0) = 0").fetchone()[0]
        junction_count = fresh_db.execute(
            "SELECT COUNT(*) FROM market_payment_methods mpm "
            "JOIN payment_methods pm "
            "  ON pm.id = mpm.payment_method_id "
            "WHERE COALESCE(pm.is_system, 0) = 0").fetchone()[0]
        assert junction_count == market_count * method_count, (
            f"Each market should have every payment method "
            f"assigned.  Got {junction_count} junctions vs "
            f"expected {market_count * method_count}.")

    def test_reward_rule_references_seeded_methods(self, fresh_db):
        """The reward rule's source/target IDs must exist in
        payment_methods — broken FK would silently disable the
        rule's effect at confirm time."""
        _run_seed(fresh_db)
        # If the JOIN in the previous test class succeeded, this
        # is structurally proven.  Pinning explicitly here too so a
        # future seed reorder doesn't silently drop the rule.
        broken = fresh_db.execute("""
            SELECT rr.id FROM reward_rules rr
            LEFT JOIN payment_methods src
              ON rr.source_method_id = src.id
            LEFT JOIN payment_methods tgt
              ON rr.reward_method_id = tgt.id
            WHERE src.id IS NULL OR tgt.id IS NULL
        """).fetchall()
        assert broken == [], (
            f"Reward rule(s) reference non-existent payment "
            f"methods: {broken}")
