"""Customer order CRUD — groups multiple receipts per customer visit."""

from fam.database.connection import get_connection


def generate_customer_label(market_day_id: int) -> str:
    """Generate a sequential customer label like C-001 for the given market day."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) FROM customer_orders WHERE market_day_id=?",
        (market_day_id,)
    ).fetchone()
    next_num = (row[0] if row else 0) + 1
    return f"C-{next_num:03d}"


def create_customer_order(market_day_id: int) -> tuple:
    """Create a new customer order. Returns (order_id, customer_label)."""
    conn = get_connection()
    label = generate_customer_label(market_day_id)
    cursor = conn.execute(
        "INSERT INTO customer_orders (market_day_id, customer_label) VALUES (?, ?)",
        (market_day_id, label)
    )
    conn.commit()
    return cursor.lastrowid, label


def get_customer_order(order_id: int) -> dict | None:
    """Get a customer order by ID with market info."""
    conn = get_connection()
    row = conn.execute("""
        SELECT co.*, md.date as market_day_date, m.name as market_name
        FROM customer_orders co
        JOIN market_days md ON co.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        WHERE co.id=?
    """, (order_id,)).fetchone()
    return dict(row) if row else None


def get_order_transactions(order_id: int) -> list:
    """Get all non-voided transactions for a customer order."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT t.*, v.name as vendor_name
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        WHERE t.customer_order_id=? AND t.status != 'Voided'
        ORDER BY t.created_at
    """, (order_id,)).fetchall()
    return [dict(r) for r in rows]


def get_order_total(order_id: int) -> float:
    """Sum of receipt_total for all non-voided transactions in the order."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(SUM(receipt_total), 0) FROM transactions "
        "WHERE customer_order_id=? AND status != 'Voided'",
        (order_id,)
    ).fetchone()
    return row[0] if row else 0.0


def get_order_vendor_summary(order_id: int) -> list:
    """Return vendor-level receipt totals for the order."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT v.name as vendor_name, SUM(t.receipt_total) as vendor_total
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        WHERE t.customer_order_id=? AND t.status != 'Voided'
        GROUP BY v.id, v.name
        ORDER BY v.name
    """, (order_id,)).fetchall()
    return [dict(r) for r in rows]


def update_customer_order_status(order_id: int, status: str):
    """Update the status of a customer order."""
    conn = get_connection()
    conn.execute(
        "UPDATE customer_orders SET status=? WHERE id=?",
        (status, order_id)
    )
    conn.commit()


def void_customer_order(order_id: int):
    """Void all transactions in the order and mark order as Voided."""
    conn = get_connection()
    conn.execute(
        "UPDATE transactions SET status='Voided' WHERE customer_order_id=? AND status='Draft'",
        (order_id,)
    )
    conn.execute(
        "UPDATE customer_orders SET status='Voided' WHERE id=?",
        (order_id,)
    )
    conn.commit()
