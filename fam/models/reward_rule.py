"""CRUD operations for ``reward_rules`` and the order-level
source-total query that drives reward derivation.

Rewards are a marketing/loyalty add-on that runs entirely outside
the financial pipeline — see ``fam/utils/rewards.py`` for the pure
math and the long disclaimer about what this does NOT touch.

This model layer is intentionally small:
  * ``get_all_reward_rules`` / ``get_active_reward_rules``
  * ``create_reward_rule`` / ``update_reward_rule`` / ``delete_reward_rule``
  * ``get_order_source_totals_by_method`` — the one query the
    rewards engine needs to compute per-order rewards.
"""

import logging

from fam.database.connection import get_connection

logger = logging.getLogger('fam.models.reward_rule')


def get_all_reward_rules() -> list[dict]:
    """All reward rules, including inactive — for the Settings UI."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM reward_rules ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_active_reward_rules() -> list[dict]:
    """Active rules only — used by the rewards engine.

    Inactive rules are kept in the table (don't delete them on
    toggle) so coordinators can re-enable later without re-typing
    the threshold/reward.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM reward_rules WHERE is_active = 1 ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_reward_rule_by_id(rule_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM reward_rules WHERE id = ?", (rule_id,)
    ).fetchone()
    return dict(row) if row else None


def create_reward_rule(
    *,
    source_method_id: int,
    threshold_cents: int,
    reward_method_id: int,
    reward_unit_cents: int,
    is_active: int = 1,
    changed_by: str = 'System',
) -> int:
    """Insert a new reward rule.

    The schema CHECK constraints guarantee ``threshold_cents > 0``,
    ``reward_unit_cents > 0``, and ``source_method_id !=
    reward_method_id``.  The "reward must be a denominated method"
    rule is enforced one layer up (UI / model validation) since
    SQLite can't reach across to ``payment_methods.denomination``
    in a CHECK.

    Raises ``sqlite3.IntegrityError`` on constraint violation.

    v1.9.10 follow-up (2026-05-01): writes an audit_log CREATE row.
    Reward rules govern what physical scrip every future customer
    gets — silent edits would erase the trail of who set what.
    """
    if not _is_denominated_method(reward_method_id):
        raise ValueError(
            "reward_method must be a denominated payment method "
            "(physical scrip the FAM rep can hand out — Food Bucks, "
            "Food RX, JH Tokens).  SNAP / Cash / FMNP / etc. cannot "
            "be reward methods.")
    from fam.models.audit import log_action
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO reward_rules"
        " (source_method_id, threshold_cents, reward_method_id,"
        "  reward_unit_cents, is_active)"
        " VALUES (?, ?, ?, ?, ?)",
        (source_method_id, threshold_cents, reward_method_id,
         reward_unit_cents, 1 if is_active else 0)
    )
    new_id = cur.lastrowid
    log_action(
        'reward_rules', new_id, 'CREATE', changed_by,
        new_value=(f"src={source_method_id} threshold={threshold_cents} "
                   f"reward={reward_method_id} unit={reward_unit_cents} "
                   f"active={is_active}"),
        commit=False)
    conn.commit()
    return new_id


def update_reward_rule(
    rule_id: int,
    *,
    source_method_id: int | None = None,
    threshold_cents: int | None = None,
    reward_method_id: int | None = None,
    reward_unit_cents: int | None = None,
    is_active: int | None = None,
    changed_by: str = 'System',
) -> None:
    """Patch fields on an existing rule (only non-None args
    are written).

    v1.9.10 follow-up (2026-05-01): writes one audit_log UPDATE row
    per changed field so the rule-edit history is reconstructible
    from the audit trail.
    """
    from fam.models.audit import log_action
    conn = get_connection()
    before_row = conn.execute(
        "SELECT * FROM reward_rules WHERE id = ?", (rule_id,)
    ).fetchone()
    if not before_row:
        return
    before = dict(before_row)
    sets = []
    params: list = []
    audit_pairs: list[tuple[str, object, object]] = []
    if source_method_id is not None:
        sets.append("source_method_id = ?")
        params.append(source_method_id)
        if source_method_id != before.get('source_method_id'):
            audit_pairs.append(
                ('source_method_id', before.get('source_method_id'),
                 source_method_id))
    if threshold_cents is not None:
        sets.append("threshold_cents = ?")
        params.append(threshold_cents)
        if threshold_cents != before.get('threshold_cents'):
            audit_pairs.append(
                ('threshold_cents', before.get('threshold_cents'),
                 threshold_cents))
    if reward_method_id is not None:
        if not _is_denominated_method(reward_method_id):
            raise ValueError(
                "reward_method must be a denominated payment method")
        sets.append("reward_method_id = ?")
        params.append(reward_method_id)
        if reward_method_id != before.get('reward_method_id'):
            audit_pairs.append(
                ('reward_method_id', before.get('reward_method_id'),
                 reward_method_id))
    if reward_unit_cents is not None:
        sets.append("reward_unit_cents = ?")
        params.append(reward_unit_cents)
        if reward_unit_cents != before.get('reward_unit_cents'):
            audit_pairs.append(
                ('reward_unit_cents', before.get('reward_unit_cents'),
                 reward_unit_cents))
    if is_active is not None:
        new_active = 1 if is_active else 0
        sets.append("is_active = ?")
        params.append(new_active)
        if new_active != before.get('is_active'):
            audit_pairs.append(
                ('is_active', before.get('is_active'), new_active))
    if not sets:
        return
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(rule_id)
    conn.execute(
        f"UPDATE reward_rules SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    for fld, old, new in audit_pairs:
        log_action('reward_rules', rule_id, 'UPDATE', changed_by,
                   field_name=fld, old_value=old, new_value=new,
                   commit=False)
    conn.commit()


def delete_reward_rule(rule_id: int, changed_by: str = 'System') -> None:
    """Hard-delete a rule.  Use ``update_reward_rule(is_active=0)``
    to disable without losing the config.

    v1.9.10 follow-up (2026-05-01): writes an audit_log DELETE row
    capturing the rule's contents at delete time so the trail
    survives the row's removal.
    """
    from fam.models.audit import log_action
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM reward_rules WHERE id = ?", (rule_id,)
    ).fetchone()
    if not row:
        return
    snapshot = dict(row)
    conn.execute("DELETE FROM reward_rules WHERE id = ?", (rule_id,))
    log_action(
        'reward_rules', rule_id, 'DELETE', changed_by,
        old_value=(f"src={snapshot.get('source_method_id')} "
                   f"threshold={snapshot.get('threshold_cents')} "
                   f"reward={snapshot.get('reward_method_id')} "
                   f"unit={snapshot.get('reward_unit_cents')} "
                   f"active={snapshot.get('is_active')}"),
        commit=False)
    conn.commit()


def _is_denominated_method(method_id: int) -> bool:
    """Validation helper: a payment method is reward-eligible only
    if it has a non-NULL, non-zero ``denomination`` (physical scrip
    that comes in fixed face values — Food Bucks $2, Food RX $10,
    JH Tokens $X).  Non-denominated methods (SNAP, Cash, FMNP) can
    NOT be reward methods because the rep doesn't physically hand
    them out as scrip."""
    conn = get_connection()
    row = conn.execute(
        "SELECT denomination FROM payment_methods WHERE id = ?",
        (method_id,)
    ).fetchone()
    return bool(row and row['denomination'] and row['denomination'] > 0)


# ──────────────────────────────────────────────────────────────────
# Order-level source-total query
# ──────────────────────────────────────────────────────────────────


def get_order_source_totals_by_method(
    customer_order_id: int,
) -> dict[int, int]:
    """Sum ``customer_charged`` per payment method across all the
    confirmed/adjusted transactions belonging to one customer order.

    Voided transactions are EXCLUDED — if the cashier voids a
    receipt later, the rewards report recomputes against the
    remaining confirmed source totals.  (Receipt printed at the
    time-of-payment is a historical artifact; the report shows
    current state.)

    Returns ``{payment_method_id -> total_cents}``.  Methods with
    zero contribution are omitted.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT pli.payment_method_id AS pm_id,
               COALESCE(SUM(pli.customer_charged), 0) AS total
        FROM payment_line_items pli
        JOIN transactions t ON pli.transaction_id = t.id
        WHERE t.customer_order_id = ?
          AND t.status IN ('Confirmed', 'Adjusted')
        GROUP BY pli.payment_method_id
        HAVING SUM(pli.customer_charged) > 0
        """,
        (customer_order_id,)
    ).fetchall()
    return {r['pm_id']: r['total'] for r in rows}


def get_method_lookup() -> dict[int, dict]:
    """Quick ``{id -> method-row}`` map used by the rewards engine
    for display-name resolution."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, denomination FROM payment_methods"
    ).fetchall()
    return {r['id']: dict(r) for r in rows}
