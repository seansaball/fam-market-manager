"""Transaction and payment line item CRUD operations."""

import logging
from datetime import datetime
from fam.database.connection import get_connection
from fam.models.audit import log_action

logger = logging.getLogger('fam.models.transaction')


def generate_transaction_id(market_day_date: str) -> str:
    """Generate a unique FAM-YYYYMMDD-NNNN transaction ID."""
    conn = get_connection()
    date_part = market_day_date.replace("-", "")
    prefix = f"FAM-{date_part}-"

    # Find the highest sequence number for this date
    row = conn.execute(
        "SELECT fam_transaction_id FROM transactions WHERE fam_transaction_id LIKE ? ORDER BY fam_transaction_id DESC LIMIT 1",
        (prefix + "%",)
    ).fetchone()

    if row:
        last_seq = int(row[0].split("-")[-1])
        next_seq = last_seq + 1
    else:
        next_seq = 1

    return f"{prefix}{next_seq:04d}"


def create_transaction(market_day_id, vendor_id, receipt_total, receipt_number=None,
                       market_day_date=None, notes=None, customer_order_id=None):
    """Create a new draft transaction. Returns (transaction_id, fam_transaction_id)."""
    conn = get_connection()

    if market_day_date is None:
        row = conn.execute("SELECT date FROM market_days WHERE id=?", (market_day_id,)).fetchone()
        market_day_date = row[0]

    fam_tid = generate_transaction_id(market_day_date)

    cursor = conn.execute(
        """INSERT INTO transactions (fam_transaction_id, market_day_id, vendor_id,
           receipt_total, receipt_number, notes, customer_order_id, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'Draft')""",
        (fam_tid, market_day_id, vendor_id, receipt_total, receipt_number, notes,
         customer_order_id)
    )
    conn.commit()
    txn_id = cursor.lastrowid

    log_action('transactions', txn_id, 'CREATE', 'System',
               notes=f"Created {fam_tid} total=${receipt_total:.2f} vendor={vendor_id}")
    logger.info("Transaction created: %s id=%s total=$%.2f", fam_tid, txn_id, receipt_total)
    return txn_id, fam_tid


def get_transaction_by_id(txn_id):
    conn = get_connection()
    row = conn.execute("""
        SELECT t.*, v.name as vendor_name, md.date as market_day_date,
               m.name as market_name
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        WHERE t.id=?
    """, (txn_id,)).fetchone()
    return dict(row) if row else None


def get_transaction_by_fam_id(fam_transaction_id):
    conn = get_connection()
    row = conn.execute("""
        SELECT t.*, v.name as vendor_name, md.date as market_day_date,
               m.name as market_name
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        WHERE t.fam_transaction_id=?
    """, (fam_transaction_id,)).fetchone()
    return dict(row) if row else None


def update_transaction(txn_id, commit=True, **kwargs):
    """Update transaction fields. Supports: receipt_total, vendor_id, receipt_number,
    status, snap_reference_code, notes.

    When *commit* is False the caller is responsible for committing.
    """
    conn = get_connection()
    allowed = {'receipt_total', 'vendor_id', 'receipt_number', 'status',
               'snap_reference_code', 'notes', 'confirmed_by', 'confirmed_at',
               'customer_order_id'}
    fields = []
    values = []
    for key, value in kwargs.items():
        if key in allowed:
            fields.append(f"{key}=?")
            values.append(value)
    if not fields:
        return
    values.append(txn_id)
    conn.execute(f"UPDATE transactions SET {', '.join(fields)} WHERE id=?", values)
    if commit:
        conn.commit()


def confirm_transaction(txn_id, confirmed_by="Volunteer", commit=True):
    """Set transaction status to Confirmed."""
    now = datetime.now().isoformat()
    update_transaction(txn_id, commit=commit, status='Confirmed',
                       confirmed_by=confirmed_by, confirmed_at=now)
    log_action('transactions', txn_id, 'CONFIRM', confirmed_by,
               notes='Payment confirmed', commit=commit)
    logger.info("Transaction confirmed: id=%s by=%s", txn_id, confirmed_by)


def void_transaction(txn_id):
    """Void a transaction (soft delete)."""
    update_transaction(txn_id, status='Voided')


def get_draft_transactions(market_day_id):
    """Get all draft transactions for a market day."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT t.*, v.name as vendor_name
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        WHERE t.market_day_id=? AND t.status='Draft'
        ORDER BY t.created_at
    """, (market_day_id,)).fetchall()
    return [dict(r) for r in rows]


def search_transactions(market_day_id=None, vendor_id=None, status=None, fam_id_search=None):
    """Search transactions with optional filters."""
    conn = get_connection()
    query = """
        SELECT t.*, v.name as vendor_name, md.date as market_day_date,
               m.name as market_name,
               co.customer_label as customer_label
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        LEFT JOIN customer_orders co ON t.customer_order_id = co.id
        WHERE 1=1
    """
    params = []
    if market_day_id:
        query += " AND t.market_day_id=?"
        params.append(market_day_id)
    if vendor_id:
        query += " AND t.vendor_id=?"
        params.append(vendor_id)
    if status:
        query += " AND t.status=?"
        params.append(status)
    if fam_id_search:
        query += " AND t.fam_transaction_id LIKE ?"
        params.append(f"%{fam_id_search}%")
    query += " ORDER BY t.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# --- Payment line items ---

def save_payment_line_items(transaction_id, line_items, commit=True):
    """Save payment line items for a transaction. Replaces existing items.

    When *commit* is False the caller is responsible for committing.
    """
    conn = get_connection()
    conn.execute("DELETE FROM payment_line_items WHERE transaction_id=?", (transaction_id,))
    for item in line_items:
        conn.execute(
            """INSERT INTO payment_line_items
               (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
                method_amount, match_amount, customer_charged)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                transaction_id,
                item['payment_method_id'],
                item['method_name_snapshot'],
                item['match_percent_snapshot'],
                item['method_amount'],
                item['match_amount'],
                item['customer_charged'],
            )
        )
    if commit:
        conn.commit()

    methods_summary = ", ".join(
        f"{it['method_name_snapshot']}=${it['method_amount']:.2f}" for it in line_items
    )
    log_action('payment_line_items', transaction_id, 'PAYMENT_SAVED', 'System',
               notes=methods_summary, commit=commit)
    logger.info("Payment lines saved: txn=%s items=%d", transaction_id, len(line_items))


def get_payment_line_items(transaction_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM payment_line_items WHERE transaction_id=? ORDER BY id",
        (transaction_id,)
    ).fetchall()
    return [dict(r) for r in rows]
