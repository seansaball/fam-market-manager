"""FMNP entry CRUD operations."""

from fam.database.connection import get_connection
from fam.utils.timezone import eastern_timestamp

# Sentinel to distinguish "not provided" from None (which means "clear the photo")
_UNSET = object()


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
                      check_count=None, notes=None, photo_path=None):
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO fmnp_entries
           (market_day_id, vendor_id, amount, check_count, notes, entered_by, photo_path, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (market_day_id, vendor_id, amount, check_count, notes, entered_by, photo_path,
         eastern_timestamp())
    )
    conn.commit()
    return cursor.lastrowid


def update_fmnp_entry(entry_id, amount=None, vendor_id=None,
                      check_count=None, notes=None, photo_path=_UNSET):
    conn = get_connection()
    fields = []
    values = []
    if amount is not None:
        fields.append("amount=?")
        values.append(amount)
    if vendor_id is not None:
        fields.append("vendor_id=?")
        values.append(vendor_id)
    if check_count is not None:
        fields.append("check_count=?")
        values.append(check_count)
    if notes is not None:
        fields.append("notes=?")
        values.append(notes)
    if photo_path is not _UNSET:
        fields.append("photo_path=?")
        values.append(photo_path)
    fields.append("updated_at=?")
    values.append(eastern_timestamp())
    values.append(entry_id)
    conn.execute(f"UPDATE fmnp_entries SET {', '.join(fields)} WHERE id=?", values)
    conn.commit()


def delete_fmnp_entry(entry_id):
    """Soft-delete an FMNP entry by setting status to 'Deleted'."""
    conn = get_connection()
    conn.execute(
        "UPDATE fmnp_entries SET status = 'Deleted', updated_at = ? WHERE id = ?",
        (eastern_timestamp(), entry_id),
    )
    conn.commit()


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
