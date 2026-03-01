"""Audit log operations (append-only)."""

import logging

from fam.database.connection import get_connection

logger = logging.getLogger('fam.models.audit')

# Human-readable labels for audit action codes
ACTION_LABELS = {
    'CREATE':        'Transaction Created',
    'CONFIRM':       'Payment Confirmed',
    'ADJUST':        'Transaction Adjusted',
    'VOID':          'Voided',
    'PAYMENT_SAVED': 'Payment Methods Saved',
    'OPEN':          'Market Day Opened',
    'CLOSE':         'Market Day Closed',
    'REOPEN':        'Market Day Reopened',
    'INSERT':        'Record Added',
    'DELETE':        'Record Removed',
    'UPDATE':        'Record Updated',
}


def log_action(table_name, record_id, action, changed_by,
               field_name=None, old_value=None, new_value=None,
               reason_code=None, notes=None, commit=True):
    """Write an entry to the audit log. Append-only.

    When *commit* is False the caller is responsible for committing.
    """
    conn = get_connection()
    conn.execute(
        """INSERT INTO audit_log
           (table_name, record_id, action, field_name, old_value, new_value,
            reason_code, notes, changed_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (table_name, record_id, action, field_name,
         str(old_value) if old_value is not None else None,
         str(new_value) if new_value is not None else None,
         reason_code, notes, changed_by)
    )
    if commit:
        conn.commit()
    logger.info("audit: %s %s id=%s by=%s", action, table_name, record_id, changed_by)


def get_audit_log(table_name=None, record_id=None, limit=100):
    """Retrieve audit log entries with optional filters."""
    conn = get_connection()
    query = "SELECT * FROM audit_log WHERE 1=1"
    params = []
    if table_name:
        query += " AND table_name=?"
        params.append(table_name)
    if record_id:
        query += " AND record_id=?"
        params.append(record_id)
    query += " ORDER BY changed_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_transaction_log(market_day_id=None, date_from=None, date_to=None,
                        action_filter=None, limit=500):
    """Retrieve audit log entries with transaction context for human-readable display.

    LEFT JOINs audit_log → transactions → vendors → market_days → markets
    to enrich each entry with FAM transaction ID, vendor name, and market info.

    Args:
        market_day_id: Filter to entries related to a specific market day.
        date_from: Include entries from this date (inclusive, 'YYYY-MM-DD').
        date_to: Include entries to this date (inclusive, 'YYYY-MM-DD').
        action_filter: List of action strings to include (e.g. ['CREATE', 'CONFIRM']).
                       If None, all actions are included.
        limit: Max rows to return (default 500).

    Returns:
        List of dicts with keys: id, changed_at, action, table_name, record_id,
        fam_transaction_id, vendor_name, market_name, market_day_date,
        field_name, old_value, new_value, reason_code, notes, changed_by
    """
    conn = get_connection()
    query = """
        SELECT al.id, al.changed_at, al.action, al.table_name, al.record_id,
               al.field_name, al.old_value, al.new_value, al.reason_code,
               al.notes, al.changed_by,
               t.fam_transaction_id, v.name AS vendor_name,
               m.name AS market_name, md.date AS market_day_date
        FROM audit_log al
        LEFT JOIN transactions t
            ON al.record_id = t.id
            AND al.table_name IN ('transactions', 'payment_line_items')
        LEFT JOIN vendors v ON t.vendor_id = v.id
        LEFT JOIN market_days md ON t.market_day_id = md.id
        LEFT JOIN markets m ON md.market_id = m.id
        WHERE al.table_name IN (
            'transactions', 'payment_line_items',
            'customer_orders', 'market_days', 'fmnp_entries'
        )
    """
    params = []

    if market_day_id:
        query += " AND (md.id = ? OR (al.table_name = 'market_days' AND al.record_id = ?))"
        params.extend([market_day_id, market_day_id])

    if date_from:
        query += " AND al.changed_at >= ?"
        params.append(date_from)

    if date_to:
        query += " AND al.changed_at < date(?, '+1 day')"
        params.append(date_to)

    if action_filter:
        placeholders = ', '.join('?' for _ in action_filter)
        query += f" AND al.action IN ({placeholders})"
        params.extend(action_filter)

    query += " ORDER BY al.changed_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]
