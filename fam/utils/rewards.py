"""Customer-facing rewards program — pure derivation logic.

The rewards program is a marketing/loyalty add-on that exists
**entirely outside the financial pipeline**:

    For every <threshold> dollars the customer pays in <source method>
    on a single order, the FAM rep hands them <reward_unit> dollars'
    worth of <reward method> as physical scrip.

Example default rule (seeded on fresh install):
    For every $5 of SNAP customer_charged in an order →
    hand the customer one $2 JH Food Bucks token.

──────────────────────────────────────────────────────────────────
WHAT THIS MODULE DOES NOT DO
──────────────────────────────────────────────────────────────────
* It does NOT modify ``transactions`` or ``payment_line_items``.
* It does NOT participate in vendor reimbursement, FAM match, the
  daily match cap, or the per-line invariant.
* It does NOT persist any reward amount.  Rewards are derived
  on demand from the source-method ``customer_charged`` totals
  already in the DB.
* It does NOT affect the engine, the auto-distribute, the
  resolve_payment_state pipeline, or any safety guard.

Rewards surface in EXACTLY three places:
  1. Payment-confirmation dialog (cashier sees what to hand out).
  2. Printed receipt (customer's record of what they got).
  3. The "Generated Rewards" report (recomputed view, not stored).

──────────────────────────────────────────────────────────────────
MATH (whole-increment, NOT pro-rated)
──────────────────────────────────────────────────────────────────

For each active rule R:

    n_units      = floor(source_total_cents / R.threshold_cents)
    reward_cents = n_units × R.reward_unit_cents

If ``source_total_cents`` is below ``R.threshold_cents``, ``n_units``
is 0 and the rule yields no reward — partial thresholds earn nothing.

Source totals are summed PER CUSTOMER ORDER across all confirmed/
adjusted transactions (voided txns excluded), restricted to line
items whose payment method matches the rule's source method.
"""

from typing import Iterable, NamedTuple


class RewardLine(NamedTuple):
    """One line of generated reward for a single (order, rule)."""
    rule_id: int
    source_method_id: int
    source_method_name: str
    source_total_cents: int          # sum of customer_charged for this method on this order
    threshold_cents: int             # rule's threshold (informational)
    reward_method_id: int
    reward_method_name: str
    reward_unit_cents: int           # rule's per-unit reward (informational)
    n_units: int                     # how many full thresholds were met
    reward_total_cents: int          # = n_units × reward_unit_cents


def compute_reward_for_rule(
    source_total_cents: int,
    threshold_cents: int,
    reward_unit_cents: int,
) -> tuple[int, int]:
    """Pure computation: how many full reward units does
    ``source_total_cents`` earn under a (threshold, reward_unit) rule?

    Returns ``(n_units, reward_total_cents)``.  Whole-increment math:
    a $7 source under a $5 threshold yields 1 unit, NOT 1.4.

    Defensive against degenerate inputs:
      * ``source_total_cents < 0`` → (0, 0)  (shouldn't happen but
        clamp rather than crash on a corrupt row)
      * ``threshold_cents <= 0`` → (0, 0)  (skip the rule;
        the schema CHECK should make this impossible but we
        defend anyway)
    """
    if source_total_cents < 0 or threshold_cents <= 0:
        return (0, 0)
    n_units = source_total_cents // threshold_cents
    return (n_units, n_units * reward_unit_cents)


def compute_rewards_for_order(
    order_source_totals_by_method: dict[int, int],
    active_rules: Iterable[dict],
    method_lookup: dict[int, dict],
) -> list[RewardLine]:
    """Apply every active rule to one customer order's source totals.

    Args:
        order_source_totals_by_method: ``{payment_method_id ->
            sum(customer_charged) for that method on the order}``.
            Caller is responsible for producing this dict from the
            DB (see ``fam/models/reward_rule.py``); this function is
            pure so it can be unit-tested in isolation.
        active_rules: iterable of dicts with keys ``id``,
            ``source_method_id``, ``threshold_cents``,
            ``reward_method_id``, ``reward_unit_cents``.
        method_lookup: ``{payment_method_id -> {'name': ..., ...}}``
            for resolving display names.

    Returns:
        List of ``RewardLine``, ONE per rule that produced ≥ 1 unit.
        Rules whose source total is below threshold are dropped from
        the result (they contribute nothing).  Order is the input
        rule order so callers get deterministic display ordering.
    """
    out: list[RewardLine] = []
    for rule in active_rules:
        src_id = rule['source_method_id']
        source_total = order_source_totals_by_method.get(src_id, 0)
        n_units, reward_total = compute_reward_for_rule(
            source_total,
            rule['threshold_cents'],
            rule['reward_unit_cents'],
        )
        if n_units == 0:
            continue
        src_meta = method_lookup.get(src_id, {})
        rwd_meta = method_lookup.get(rule['reward_method_id'], {})
        out.append(RewardLine(
            rule_id=rule['id'],
            source_method_id=src_id,
            source_method_name=src_meta.get('name', f"#{src_id}"),
            source_total_cents=source_total,
            threshold_cents=rule['threshold_cents'],
            reward_method_id=rule['reward_method_id'],
            reward_method_name=rwd_meta.get(
                'name', f"#{rule['reward_method_id']}"),
            reward_unit_cents=rule['reward_unit_cents'],
            n_units=n_units,
            reward_total_cents=reward_total,
        ))
    return out


def format_reward_line_for_display(line: RewardLine) -> str:
    """Human-readable single-line description of a reward.

    Used in the payment-confirmation dialog and the receipt.

    Format:
        "$5.00 of SNAP → 1 × $2.00 JH Food Bucks ($2.00)"
    """
    return (
        f"${line.source_total_cents/100:.2f} of "
        f"{line.source_method_name} → "
        f"{line.n_units} × ${line.reward_unit_cents/100:.2f} "
        f"{line.reward_method_name} "
        f"(${line.reward_total_cents/100:.2f})"
    )
