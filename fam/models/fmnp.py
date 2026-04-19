"""FMNP entry CRUD operations.

All mutations (create, update, delete) emit audit log entries so that
FMNP reimbursement history is fully reconstructible after the fact.
UPDATE emits one audit row per changed field so the forensic trail
captures exactly what was changed and what the previous value was.
"""

import logging

from fam.database.connection import get_connection
from fam.models.audit import log_action
from fam.utils.timezone import eastern_timestamp

logger = logging.getLogger('fam.models.fmnp')

# Sentinel to distinguish "not provided" from None (which means "clear the photo")
_UNSET = object()

# Fields that participate in the UPDATE audit diff (in logical order)
_AUDITED_FIELDS = ('amount', 'vendor_id', 'check_count', 'notes', 'photo_path')


def get_fmnp_entries(market_day_id=None, active_only=True):
    conn = get_connection()
    status_filter = "AND f.status = 'Active'" if active_only else ""
    if market_day_id:
        rows = conn.execute(f"""
            SELECT f.*, v.name as vendor_name, md.date as market_day_date,
                   m.name as market_name
            FROM fmnp_entries f
            JOIN vendors v ON f.vendor_id = v.id
            JOIN market_days md ON f.market_day_id = md.id
            JOIN markets m ON md.market_id = m.id
            WHERE f.market_day_id=? {status_filter}
            ORDER BY f.created_at DESC
        """, (market_day_id,)).fetchall()
    else:
        where = "WHERE f.status = 'Active'" if active_only else ""
        rows = conn.execute(f"""
            SELECT f.*, v.name as vendor_name, md.date as market_day_date,
                   m.name as market_name
            FROM fmnp_entries f
            JOIN vendors v ON f.vendor_id = v.id
            JOIN market_days md ON f.market_day_id = md.id
            JOIN markets m ON md.market_id = m.id
            {where}
            ORDER BY f.created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


def get_fmnp_entry_by_id(entry_id):
    conn = get_connection()
    row = conn.execute("""
        SELECT f.*, v.name as vendor_name
        FROM fmnp_entries f
        JOIN vendors v ON f.vendor_id = v.id
        WHERE f.id=?
    """, (entry_id,)).fetchone()
    return dict(row) if row else None


def create_fmnp_entry(market_day_id, vendor_id, amount, entered_by,
                      check_count=None, notes=None, photo_path=None,
                      commit=True):
    """Create a new FMNP entry and log the action to the audit trail.

    The audit row's ``changed_by`` reflects *entered_by*, matching the
    human-readable person who submitted the FMNP check.  The insert and
    audit-log write happen in a single transaction when *commit* is True.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO fmnp_entries
               (market_day_id, vendor_id, amount, check_count, notes, entered_by, photo_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (market_day_id, vendor_id, amount, check_count, notes, entered_by, photo_path,
             eastern_timestamp())
        )
        entry_id = cursor.lastrowid
        note_parts = [f"vendor_id={vendor_id}",
                      f"amount=${amount/100:.2f}"]
        if check_count is not None:
            note_parts.append(f"check_count={check_count}")
        log_action('fmnp_entries', entry_id, 'INSERT', entered_by,
                   notes="FMNP entry created: " + ", ".join(note_parts),
                   commit=False)
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    logger.info("FMNP entry created: id=%s vendor_id=%s amount=%sc by=%s",
                entry_id, vendor_id, amount, entered_by)
    return entry_id


def update_fmnp_entry(entry_id, amount=None, vendor_id=None,
                      check_count=None, notes=None, photo_path=_UNSET,
                      changed_by="System", commit=True):
    """Update an FMNP entry and log each changed field to the audit trail.

    Only fields whose value actually changes trigger both a DB update
    and an audit row.  If nothing changed, no audit rows are emitted
    and the ``updated_at`` timestamp is not bumped.  When *commit* is
    False the caller owns the transaction.
    """
    conn = get_connection()

    # Snapshot current values so we can diff and produce per-field audit rows
    row = conn.execute(
        "SELECT amount, vendor_id, check_count, notes, photo_path "
        "FROM fmnp_entries WHERE id=?", (entry_id,)
    ).fetchone()
    if row is None:
        logger.warning("update_fmnp_entry: entry_id=%s not found", entry_id)
        return

    # Collect (field_name, old_value, new_value) for each genuine change
    changes = []

    def _diff(field, incoming, sentinel_unset=False):
        if sentinel_unset and incoming is _UNSET:
            return
        if not sentinel_unset and incoming is None:
            return
        if incoming != row[field]:
            changes.append((field, row[field], incoming))

    _diff('amount',       amount)
    _diff('vendor_id',    vendor_id)
    _diff('check_count',  check_count)
    _diff('notes',        notes)
    _diff('photo_path',   photo_path, sentinel_unset=True)

    if not changes:
        return  # No-op — don't bump updated_at, don't flood audit log

    try:
        set_clauses = [f"{name}=?" for name, _, _ in changes]
        set_values = [new for _, _, new in changes]
        set_clauses.append("updated_at=?")
        set_values.append(eastern_timestamp())
        set_values.append(entry_id)
        conn.execute(
            f"UPDATE fmnp_entries SET {', '.join(set_clauses)} WHERE id=?",
            set_values,
        )

        # One audit row per changed field preserves full forensic detail
        for field_name, old_val, new_val in changes:
            log_action('fmnp_entries', entry_id, 'UPDATE', changed_by,
                       field_name=field_name,
                       old_value=old_val, new_value=new_val,
                       commit=False)

        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    logger.info("FMNP entry updated: id=%s changes=%s by=%s",
                entry_id, [c[0] for c in changes], changed_by)


def delete_fmnp_entry(entry_id, changed_by="System", commit=True):
    """Soft-delete an FMNP entry by setting status to 'Deleted'.

    The delete action is recorded in the audit log with *changed_by* so
    the trail attributes the deletion to a real person.  Same transaction
    as the status update.
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE fmnp_entries SET status = 'Deleted', updated_at = ? WHERE id = ?",
            (eastern_timestamp(), entry_id),
        )
        log_action('fmnp_entries', entry_id, 'DELETE', changed_by,
                   notes='FMNP entry soft-deleted', commit=False)
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    logger.info("FMNP entry deleted: id=%s by=%s", entry_id, changed_by)


def update_fmnp_photo_drive_url(entry_id, drive_url):
    """Store the Google Drive shareable URL(s) after successful photo upload.

    *drive_url* can be a single URL string or a JSON-encoded array of URLs
    (for multi-photo entries).
    """
    conn = get_connection()
    conn.execute(
        "UPDATE fmnp_entries SET photo_drive_url=?, updated_at=? WHERE id=?",
        (drive_url, eastern_timestamp(), entry_id)
    )
    conn.commit()


def get_fmnp_entries_with_drive_urls():
    """Return active FMNP entries that have Drive URLs set (for verification)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT id, photo_path, photo_drive_url
        FROM fmnp_entries
        WHERE photo_drive_url IS NOT NULL
          AND photo_drive_url != ''
          AND status = 'Active'
    """).fetchall()
    return [dict(r) for r in rows]


def get_deleted_fmnp_with_photos():
    """Return deleted FMNP entries that have Drive URLs (for VOID rename)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT id, photo_drive_url
        FROM fmnp_entries
        WHERE status = 'Deleted'
          AND photo_drive_url IS NOT NULL
          AND photo_drive_url != ''
    """).fetchall()
    return [dict(r) for r in rows]


def get_pending_photo_uploads():
    """Return FMNP entries that have local photo(s) with incomplete Drive uploads.

    An entry is "pending" when:
      - photo_path is set (one or more local photos)
      - photo_drive_url is NULL/empty, OR has fewer URLs than photo_path
    """
    from fam.utils.photo_paths import parse_photo_paths

    conn = get_connection()
    rows = conn.execute("""
        SELECT f.id, f.photo_path, f.photo_drive_url,
               v.name as vendor_name, md.date as market_day_date,
               m.name as market_name
        FROM fmnp_entries f
        JOIN vendors v ON f.vendor_id = v.id
        JOIN market_days md ON f.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        WHERE f.photo_path IS NOT NULL
          AND f.photo_path != ''
          AND f.status = 'Active'
    """).fetchall()

    pending = []
    for r in rows:
        local_paths = parse_photo_paths(r['photo_path'])
        drive_urls = parse_photo_paths(r['photo_drive_url'])
        if len(drive_urls) < len(local_paths):
            pending.append(dict(r))
    return pending
