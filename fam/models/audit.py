"""Audit log operations (append-only)."""

import logging

from fam.database.connection import get_connection

logger = logging.getLogger('fam.models.audit')


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
