"""Customer order CRUD — groups multiple receipts per customer visit."""

import logging
from fam.database.connection import get_connection
from fam.models.audit import log_action

logger = logging.getLogger('fam.models.customer_order')


def generate_customer_label(market_day_id: int) -> str:
    """Generate a sequential customer label like C-001 for the given market day."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) FROM customer_orders WHERE market_day_id=?",
        (market_day_id,)
    ).fetchone()
    next_num = (row[0] if row else 0) + 1
    return f"C-{next_num:03d}"


def create_customer_order(market_day_id: int, customer_label: str | None = None,
                          zip_code: str | None = None) -> tuple:
    """Create a new customer order. Returns (order_id, customer_label).

    When *customer_label* is provided (returning customer), the order reuses
    that label instead of generating a new sequential one.
    *zip_code* is an optional 5-digit US zip code for geolocation tracking.
    """
    conn = get_connection()
    label = customer_label or generate_customer_label(market_day_id)
    cursor = conn.execute(
        "INSERT INTO customer_orders (market_day_id, customer_label, zip_code)"
        " VALUES (?, ?, ?)",
        (market_day_id, label, zip_code)
    )
    conn.commit()
    order_id = cursor.lastrowid

    notes = f"Customer order {label} for market_day={market_day_id}"
    if customer_label:
        notes += " (returning customer)"
    log_action('customer_orders', order_id, 'CREATE', 'System', notes=notes)
    logger.info("Customer order created: id=%s label=%s market_day=%s returning=%s",
                order_id, label, market_day_id, bool(customer_label))
    return order_id, label


def get_customer_order(order_id: int) -> dict | None:
    """Get a customer order by ID with market info."""
    conn = get_connection()
    row = conn.execute("""
        SELECT co.*, md.date as market_day_date, m.name as market_name,
               m.daily_match_limit, m.match_limit_active
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


def update_customer_order_status(order_id: int, status: str, commit=True):
    """Update the status of a customer order.

    When *commit* is False the caller is responsible for committing.
    """
    conn = get_connection()
    conn.execute(
        "UPDATE customer_orders SET status=? WHERE id=?",
        (status, order_id)
    )
    if commit:
        conn.commit()


def update_customer_order_zip_code(order_id: int, zip_code: str | None, commit=True):
    """Update the zip code for a customer order."""
    conn = get_connection()
    conn.execute(
        "UPDATE customer_orders SET zip_code=? WHERE id=?",
        (zip_code, order_id)
    )
    if commit:
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

    log_action('customer_orders', order_id, 'VOID', 'System',
               notes='Customer order and draft transactions voided')
    logger.info("Customer order voided: id=%s", order_id)


def get_confirmed_customers_for_market_day(market_day_id: int) -> list:
    """Get distinct confirmed customer labels for a market day with their FAM match totals.

    Returns a list of dicts: {customer_label, order_count, total_match, receipt_count}.
    Only includes customers who have at least one Confirmed order.
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT co.customer_label,
               COUNT(DISTINCT co.id) as order_count,
               COUNT(t.id) as receipt_count,
               COALESCE(SUM(pli.match_amount), 0) as total_match
        FROM customer_orders co
        JOIN transactions t ON t.customer_order_id = co.id AND t.status = 'Confirmed'
        LEFT JOIN payment_line_items pli ON pli.transaction_id = t.id
        WHERE co.market_day_id = ? AND co.status = 'Confirmed'
        GROUP BY co.customer_label
        ORDER BY co.customer_label
    """, (market_day_id,)).fetchall()
    return [dict(r) for r in rows]


def get_customer_prior_match(customer_label: str, market_day_id: int,
                             exclude_order_id: int | None = None) -> float:
    """Sum the FAM match (match_amount) already used by a customer label on a market day.

    Only counts Confirmed orders.  *exclude_order_id* lets you omit the
    current order so it isn't double-counted.
    """
    conn = get_connection()
    query = """
        SELECT COALESCE(SUM(pli.match_amount), 0)
        FROM customer_orders co
        JOIN transactions t ON t.customer_order_id = co.id AND t.status = 'Confirmed'
        JOIN payment_line_items pli ON pli.transaction_id = t.id
        WHERE co.market_day_id = ?
          AND co.customer_label = ?
          AND co.status = 'Confirmed'
    """
    params = [market_day_id, customer_label]
    if exclude_order_id is not None:
        query += " AND co.id != ?"
        params.append(exclude_order_id)
    row = conn.execute(query, params).fetchone()
    return round(row[0], 2) if row else 0.0


def get_draft_orders_for_market_day(market_day_id: int) -> list:
    """Get all Draft customer orders for a market day with receipt counts and totals."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT co.id, co.customer_label, co.status, co.created_at,
               COUNT(t.id) as receipt_count,
               COALESCE(SUM(t.receipt_total), 0) as order_total
        FROM customer_orders co
        LEFT JOIN transactions t ON t.customer_order_id = co.id
                                 AND t.status != 'Voided'
        WHERE co.market_day_id = ? AND co.status = 'Draft'
        GROUP BY co.id
        ORDER BY co.created_at DESC
    """, (market_day_id,)).fetchall()
    return [dict(r) for r in rows]
