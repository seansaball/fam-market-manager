"""Persistent record of customer-facing rewards generated at
payment-confirmation time.

This is a **write-once history table**.  Rows are inserted
atomically with the payment commit and **NEVER modified after**.
That means:

  * Pre-feature transactions don't appear here (they have no rows).
  * Rule changes / deletions don't retro-apply.
  * Disabling the rewards feature does not wipe history — the
    Generated Rewards report continues to show what the rep
    actually handed out before the toggle was flipped.
  * Voiding or adjusting a transaction does NOT touch reward
    rows.  The cashier already gave the customer the scrip; the
    historical record reflects that.

The data here is **purely informational** — it does not
participate in any vendor reimbursement, FAM match, or per-line
invariant calculation.  See ``docs/FINANCIAL_FORMULA.md § 11``.
"""

import logging
from typing import Iterable

from fam.database.connection import get_connection

logger = logging.getLogger('fam.models.generated_reward')


def record_generated_rewards(
    *,
    customer_order_id: int,
    market_day_id: int,
    reward_lines: Iterable,
    generated_by: str | None = None,
    conn=None,
) -> int:
    """Persist the reward lines computed for one customer order.

    Idempotent: if reward rows already exist for this order, the
    call is a no-op (returns 0).  This prevents duplicates if the
    confirmation flow re-fires (defensive — normal flow only
    confirms an order once).

    Args:
        customer_order_id: order this snapshot belongs to.
        market_day_id: market day for filter/report joins.
        reward_lines: iterable of ``RewardLine`` namedtuples
            from ``fam.utils.rewards.compute_rewards_for_order``.
        generated_by: name of the volunteer who confirmed the
            payment (audit trail; nullable).
        conn: optional connection — caller passes the same
            connection used for the rest of the atomic save so
            insert lives in the same transaction.  When ``None``,
            grabs a fresh connection (only for tests / ad-hoc use).

    Returns:
        Number of rows inserted (0 if no rules fired or rows
        already exist).
    """
    if conn is None:
        conn = get_connection()
    # Idempotency check — historic rows already exist for this
    # order means we already wrote at first confirmation.  Don't
    # double-write on a (defensive) re-fire.
    existing = conn.execute(
        "SELECT COUNT(*) FROM generated_rewards "
        "WHERE customer_order_id = ?",
        (customer_order_id,)
    ).fetchone()[0]
    if existing > 0:
        logger.info(
            "Reward rows already exist for order %s "
            "(%d rows) — skipping write",
            customer_order_id, existing)
        return 0

    inserted = 0
    for rl in reward_lines:
        conn.execute(
            "INSERT INTO generated_rewards"
            " (customer_order_id, market_day_id, rule_id,"
            "  source_method_id, source_method_name_snapshot,"
            "  source_total_cents, threshold_cents,"
            "  reward_method_id, reward_method_name_snapshot,"
            "  reward_unit_cents, n_units, reward_total_cents,"
            "  generated_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (customer_order_id, market_day_id, rl.rule_id,
             rl.source_method_id, rl.source_method_name,
             rl.source_total_cents, rl.threshold_cents,
             rl.reward_method_id, rl.reward_method_name,
             rl.reward_unit_cents, rl.n_units,
             rl.reward_total_cents,
             generated_by),
        )
        inserted += 1
    return inserted


def get_generated_rewards_for_market_day(md_id: int) -> list[dict]:
    """All reward rows on this market day (for cloud sync)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT gr.*,
               co.customer_label,
               m.name AS market_name,
               md.date AS market_date
        FROM generated_rewards gr
        JOIN customer_orders co
          ON gr.customer_order_id = co.id
        JOIN market_days md
          ON gr.market_day_id = md.id
        JOIN markets m
          ON md.market_id = m.id
        WHERE gr.market_day_id = ?
        ORDER BY gr.id
    """, (md_id,)).fetchall()
    return [dict(r) for r in rows]


def get_generated_rewards_for_order(order_id: int) -> list[dict]:
    """Reward rows for one order (for receipt rendering)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM generated_rewards
        WHERE customer_order_id = ?
        ORDER BY id
    """, (order_id,)).fetchall()
    return [dict(r) for r in rows]


def get_all_generated_rewards() -> list[dict]:
    """Every reward row across all markets / days (for the
    Reports screen tab)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT gr.*,
               co.customer_label,
               m.name AS market_name,
               md.date AS market_date
        FROM generated_rewards gr
        JOIN customer_orders co
          ON gr.customer_order_id = co.id
        JOIN market_days md
          ON gr.market_day_id = md.id
        JOIN markets m
          ON md.market_id = m.id
        ORDER BY gr.generated_at DESC, gr.id DESC
    """).fetchall()
    return [dict(r) for r in rows]
