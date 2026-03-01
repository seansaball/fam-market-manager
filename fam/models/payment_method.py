"""Payment method CRUD operations."""

from fam.database.connection import get_connection


def get_all_payment_methods(active_only=False):
    conn = get_connection()
    if active_only:
        rows = conn.execute(
            "SELECT * FROM payment_methods WHERE is_active=1 ORDER BY sort_order, name"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM payment_methods ORDER BY sort_order, name"
        ).fetchall()
    return [dict(r) for r in rows]


def get_payment_method_by_id(pm_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM payment_methods WHERE id=?", (pm_id,)).fetchone()
    return dict(row) if row else None


def create_payment_method(name, match_percent, sort_order=0):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO payment_methods (name, match_percent, sort_order) VALUES (?, ?, ?)",
        (name, match_percent, sort_order)
    )
    conn.commit()
    return cursor.lastrowid


def get_market_payment_method_ids(market_id):
    """Get set of payment method IDs assigned to a market."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT payment_method_id FROM market_payment_methods WHERE market_id = ?",
        (market_id,)
    ).fetchall()
    return {r['payment_method_id'] for r in rows}


def get_payment_methods_for_market(market_id, active_only=True):
    """Get payment methods assigned to a specific market."""
    conn = get_connection()
    query = """
        SELECT pm.* FROM payment_methods pm
        JOIN market_payment_methods mpm ON mpm.payment_method_id = pm.id
        WHERE mpm.market_id = ?
    """
    if active_only:
        query += " AND pm.is_active = 1"
    query += " ORDER BY pm.sort_order, pm.name"
    rows = conn.execute(query, (market_id,)).fetchall()
    return [dict(r) for r in rows]


def assign_payment_method_to_market(market_id, payment_method_id):
    """Assign a payment method to a market (idempotent)."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO market_payment_methods (market_id, payment_method_id) VALUES (?, ?)",
        (market_id, payment_method_id)
    )
    conn.commit()


def unassign_payment_method_from_market(market_id, payment_method_id):
    """Remove a payment method assignment from a market."""
    conn = get_connection()
    conn.execute(
        "DELETE FROM market_payment_methods WHERE market_id = ? AND payment_method_id = ?",
        (market_id, payment_method_id)
    )
    conn.commit()


def update_payment_method(pm_id, name=None, match_percent=None, is_active=None, sort_order=None):
    conn = get_connection()
    fields = []
    values = []
    if name is not None:
        fields.append("name=?")
        values.append(name)
    if match_percent is not None:
        fields.append("match_percent=?")
        values.append(match_percent)
    if is_active is not None:
        fields.append("is_active=?")
        values.append(int(is_active))
    if sort_order is not None:
        fields.append("sort_order=?")
        values.append(sort_order)
    if not fields:
        return
    values.append(pm_id)
    conn.execute(f"UPDATE payment_methods SET {', '.join(fields)} WHERE id=?", values)
    conn.commit()
