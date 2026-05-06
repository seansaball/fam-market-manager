"""Customer order CRUD — groups multiple receipts per customer visit."""

import logging
from fam.database.connection import get_connection
from fam.models.audit import log_action
from fam.utils.timezone import eastern_timestamp

logger = logging.getLogger('fam.models.customer_order')


def generate_customer_label(market_day_id: int) -> str:
    """Generate a customer label like ``C-001-A1B`` for the given
    market day.

    Format: ``C-{NNN}-{TAG}`` where:

    * ``NNN`` is the next sequential number for this market day on
      THIS device (``COUNT(*) + 1``).  Each device has its own
      independent sequence — there's no cross-device coordination
      because customer_orders aren't synced to the central DB.
    * ``TAG`` is a 1-4 char device tag (auto-derived hash of the
      MachineGuid by default, optionally overridden in Settings →
      About this Device).  See ``app_settings.get_device_tag()``.

    The tag turns "C-005" (ambiguous across 5 laptops) into
    "C-005-A1B" / "C-005-LB1" — globally unique and identifiable to
    a specific device.  Coordinators can spot which laptop captured
    a transaction at a glance, and the synced Google Sheets reports
    no longer show duplicate-looking customer IDs from different
    devices.

    The COUNT-based sequence stays per-device-and-market-day; the
    label format is what changes.  Existing labels in the database
    (pre-v1.9.9, format ``C-NNN``) remain valid — nothing parses
    labels by structure, they're just opaque strings.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) FROM customer_orders WHERE market_day_id=?",
        (market_day_id,)
    ).fetchone()
    next_num = (row[0] if row else 0) + 1
    # Device tag is only appended when a device_id has actually been
    # captured (i.e. ``capture_device_id`` ran during normal app
    # startup in ``fam/app.py``).  When it hasn't — pure-model
    # unit tests, or extremely early startup before identity is
    # established — fall back to the legacy ``C-NNN`` format so
    # the tag never propagates a sentinel value into real data.
    # Production code paths always have a captured device_id, so
    # this branch is invisible in deployed installs.
    from fam.utils.app_settings import get_device_id, get_device_tag
    if not get_device_id():
        return f"C-{next_num:03d}"
    return f"C-{next_num:03d}-{get_device_tag()}"


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
        "INSERT INTO customer_orders (market_day_id, customer_label, zip_code, created_at)"
        " VALUES (?, ?, ?, ?)",
        (market_day_id, label, zip_code, eastern_timestamp())
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
        SELECT co.*, md.date as market_day_date, md.market_id,
               m.name as market_name,
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


# Valid status values for customer orders
VALID_ORDER_STATUSES = {'Draft', 'Confirmed', 'Voided'}

def update_customer_order_status(order_id: int, status: str,
                                  commit=True, *,
                                  changed_by: str = 'System'):
    """Update the status of a customer order.

    When *commit* is False the caller is responsible for committing.
    Raises ValueError if status is not a valid order status.

    v1.9.10 follow-up (2026-05-01): writes an audit_log UPDATE row
    when the status actually changes.  Earlier behavior left
    Draft → Confirmed → Voided transitions untraceable at the
    order level (only the per-transaction CONFIRM/VOID rows
    existed).
    """
    if status not in VALID_ORDER_STATUSES:
        raise ValueError(
            f"Invalid order status '{status}'. "
            f"Must be one of: {', '.join(sorted(VALID_ORDER_STATUSES))}"
        )
    conn = get_connection()
    before = conn.execute(
        "SELECT status FROM customer_orders WHERE id=?", (order_id,)
    ).fetchone()
    old_status = before['status'] if before else None
    conn.execute(
        "UPDATE customer_orders SET status=? WHERE id=?",
        (status, order_id)
    )
    if old_status is not None and old_status != status:
        log_action(
            'customer_orders', order_id, 'UPDATE', changed_by,
            field_name='status',
            old_value=old_status, new_value=status, commit=False)
    if commit:
        conn.commit()


def update_customer_order_zip_code(order_id: int, zip_code: str | None,
                                    commit=True, *,
                                    changed_by: str = 'System'):
    """Update the zip code for a customer order.

    v1.9.10 follow-up (2026-05-01): zip_code edits are PII edits;
    audit them so the trail of who changed customer geography
    survives.
    """
    conn = get_connection()
    before = conn.execute(
        "SELECT zip_code FROM customer_orders WHERE id=?", (order_id,)
    ).fetchone()
    old_zip = before['zip_code'] if before else None
    conn.execute(
        "UPDATE customer_orders SET zip_code=? WHERE id=?",
        (zip_code, order_id)
    )
    if (old_zip or '') != (zip_code or ''):
        log_action(
            'customer_orders', order_id, 'UPDATE', changed_by,
            field_name='zip_code',
            old_value=old_zip, new_value=zip_code, commit=False)
    if commit:
        conn.commit()


def void_customer_order(order_id: int, voided_by: str = "System"):
    """Void all transactions in the order and mark the order Voided.

    v2.0.1: every child transaction now emits its own ``VOID`` audit
    row (matching ``void_transaction``).  Earlier versions ran a
    single bulk ``UPDATE transactions … WHERE customer_order_id=?``
    and only logged ONE audit row against ``customer_orders``,
    leaving the per-transaction audit trail incomplete.  The
    Activity Log and audit-coverage tests both expect a per-txn
    VOID for every voided transaction; this restores that
    invariant for the bulk-void path.
    """
    from fam.models.transaction import update_transaction
    conn = get_connection()
    try:
        # Find every non-voided child transaction up front so the
        # loop is deterministic even if a sibling is voided
        # concurrently.
        rows = conn.execute(
            "SELECT id FROM transactions "
            "WHERE customer_order_id=? AND status != 'Voided'",
            (order_id,),
        ).fetchall()
        for r in rows:
            t_id = r['id']
            # Mirror void_transaction's pattern: status flip without
            # the per-field UPDATE audit, then a single VOID action.
            update_transaction(t_id, commit=False, status='Voided',
                               _skip_audit=True)
            log_action('transactions', t_id, 'VOID', voided_by,
                       notes='Voided as part of customer-order void',
                       commit=False)
        conn.execute(
            "UPDATE customer_orders SET status='Voided' WHERE id=?",
            (order_id,)
        )
        log_action('customer_orders', order_id, 'VOID', voided_by,
                   notes='Customer order and all transactions voided',
                   commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    logger.info("Customer order voided: id=%s txns=%d by=%s",
                order_id, len(rows), voided_by)


def get_confirmed_customers_for_market_day(market_day_id: int) -> list:
    """Get distinct confirmed customer labels for a market day with their FAM match totals.

    Returns a list of dicts: {customer_label, order_count, total_match, receipt_count}.
    Only includes customers who have at least one Confirmed or Adjusted order.
    """
    conn = get_connection()
    # v1.9.10 follow-up (2026-05-01): use COUNT(DISTINCT t.id) for
    # receipt_count.  The LEFT JOIN to payment_line_items multiplies
    # one transaction by N pli rows, and a plain ``COUNT(t.id)``
    # counts every join row — a single confirmed receipt with 3
    # methods (SNAP + Cash + Food Bucks) used to display as
    # "3 receipts" to the volunteer at the Receipt Intake screen
    # ("Customer C-001 — 3 receipt(s), $40.00 matched").  DISTINCT
    # collapses the joined rows back to one count per transaction.
    # ``total_match`` is unaffected (pli rows are 1:1 unique
    # contributions, the SUM already sums per-pli match_amount).
    rows = conn.execute("""
        SELECT co.customer_label,
               COUNT(DISTINCT co.id) as order_count,
               COUNT(DISTINCT t.id) as receipt_count,
               COALESCE(SUM(pli.match_amount), 0) as total_match
        FROM customer_orders co
        JOIN transactions t ON t.customer_order_id = co.id AND t.status IN ('Confirmed', 'Adjusted')
        LEFT JOIN payment_line_items pli ON pli.transaction_id = t.id
        WHERE co.market_day_id = ? AND co.status IN ('Confirmed', 'Adjusted')
        GROUP BY co.customer_label
        ORDER BY co.customer_label
    """, (market_day_id,)).fetchall()
    return [dict(r) for r in rows]


def get_customer_prior_match(customer_label: str, market_day_id: int,
                             exclude_order_id: int | None = None) -> int:
    """Sum the FAM match (match_amount) already used by a customer label on a market day.

    Returns integer cents.  Counts Confirmed and Adjusted orders.
    *exclude_order_id* lets you omit the current order so it isn't double-counted.
    """
    conn = get_connection()
    query = """
        SELECT COALESCE(SUM(pli.match_amount), 0)
        FROM customer_orders co
        JOIN transactions t ON t.customer_order_id = co.id AND t.status IN ('Confirmed', 'Adjusted')
        JOIN payment_line_items pli ON pli.transaction_id = t.id
        WHERE co.market_day_id = ?
          AND co.customer_label = ?
          AND co.status IN ('Confirmed', 'Adjusted')
    """
    params = [market_day_id, customer_label]
    if exclude_order_id is not None:
        query += " AND co.id != ?"
        params.append(exclude_order_id)
    row = conn.execute(query, params).fetchone()
    return int(row[0]) if row else 0


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
