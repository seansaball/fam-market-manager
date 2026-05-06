"""Audit log operations (append-only)."""

import logging

from fam import __version__ as _app_version
from fam.database.connection import get_connection
from fam.utils.timezone import eastern_timestamp

logger = logging.getLogger('fam.models.audit')

# Human-readable labels for audit action codes
ACTION_LABELS = {
    'CREATE':            'Transaction Created',
    'CONFIRM':           'Payment Confirmed',
    'ADJUST':            'Transaction Adjusted',
    'PAYMENT_ADJUSTED':  'Payment Methods Adjusted',
    'UNALLOCATED_FUNDS': 'FAM Absorbed (Customer Gone)',
    'VOID':              'Voided',
    'AUTO_CLOSE':        'Market Day Auto-Closed',
    'REWARD_ISSUED':     'Reward Tokens Issued',
    'PAYMENT_SAVED':     'Payment Methods Saved',
    'OPEN':              'Market Day Opened',
    'CLOSE':             'Market Day Closed',
    'REOPEN':            'Market Day Reopened',
    'INSERT':            'Record Added',
    'DELETE':            'Record Removed',
    'UPDATE':            'Record Updated',
}


def log_action(table_name, record_id, action, changed_by,
               field_name=None, old_value=None, new_value=None,
               reason_code=None, notes=None, commit=True):
    """Write an entry to the audit log. Append-only.

    When *commit* is False the caller is responsible for committing.
    """
    from fam.utils.app_settings import get_device_id
    conn = get_connection()
    conn.execute(
        """INSERT INTO audit_log
           (table_name, record_id, action, field_name, old_value, new_value,
            reason_code, notes, changed_by, app_version, device_id, changed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (table_name, record_id, action, field_name,
         str(old_value) if old_value is not None else None,
         str(new_value) if new_value is not None else None,
         reason_code, notes, changed_by, _app_version,
         get_device_id() or '', eastern_timestamp())
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
    # v1.9.10 follow-up (2026-05-01): ``id DESC`` is the natural
    # tiebreaker when many audit rows share a ``changed_at``
    # second.  Especially after the H8 fix made
    # ``update_transaction`` self-audit, a single Adjust call
    # writes 2-3 audit rows in the same second; without an id
    # tiebreaker the row order on read is implementation-defined
    # and tests that rely on most-recent-first see flaps.
    query += " ORDER BY changed_at DESC, id DESC LIMIT ?"
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
        customer_label, zip_code,
        field_name, old_value, new_value, reason_code, notes, changed_by

    v2.0.6: customer_label and zip_code are joined from
    ``customer_orders`` so coordinator reports can correlate audit
    actions with which customer (and which zip code) the action
    affected.  NULL when the audit row references a transaction
    with no customer_order (legacy data) or a non-transaction
    record.
    """
    conn = get_connection()
    query = """
        SELECT al.id, al.changed_at, al.action, al.table_name, al.record_id,
               al.field_name, al.old_value, al.new_value, al.reason_code,
               al.notes, al.changed_by, al.app_version, al.device_id,
               t.fam_transaction_id, v.name AS vendor_name,
               m.name AS market_name, md.date AS market_day_date,
               co.customer_label, co.zip_code
        FROM audit_log al
        LEFT JOIN transactions t
            ON al.record_id = t.id
            AND al.table_name IN ('transactions', 'payment_line_items')
        LEFT JOIN vendors v ON t.vendor_id = v.id
        LEFT JOIN market_days md ON t.market_day_id = md.id
        LEFT JOIN markets m ON md.market_id = m.id
        LEFT JOIN customer_orders co ON t.customer_order_id = co.id
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

    # See ``get_audit_log`` for the id-tiebreaker rationale.
    query += " ORDER BY al.changed_at DESC, al.id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]
