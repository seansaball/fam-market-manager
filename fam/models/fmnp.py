"""FMNP entry CRUD operations."""

from datetime import datetime
from fam.database.connection import get_connection


def get_fmnp_entries(market_day_id=None):
    conn = get_connection()
    if market_day_id:
        rows = conn.execute("""
            SELECT f.*, v.name as vendor_name, md.date as market_day_date,
                   m.name as market_name
            FROM fmnp_entries f
            JOIN vendors v ON f.vendor_id = v.id
            JOIN market_days md ON f.market_day_id = md.id
            JOIN markets m ON md.market_id = m.id
            WHERE f.market_day_id=?
            ORDER BY f.created_at DESC
        """, (market_day_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT f.*, v.name as vendor_name, md.date as market_day_date,
                   m.name as market_name
            FROM fmnp_entries f
            JOIN vendors v ON f.vendor_id = v.id
            JOIN market_days md ON f.market_day_id = md.id
            JOIN markets m ON md.market_id = m.id
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


def create_fmnp_entry(market_day_id, vendor_id, amount, entered_by, check_count=None, notes=None):
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO fmnp_entries (market_day_id, vendor_id, amount, check_count, notes, entered_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (market_day_id, vendor_id, amount, check_count, notes, entered_by)
    )
    conn.commit()
    return cursor.lastrowid


def update_fmnp_entry(entry_id, amount=None, vendor_id=None, check_count=None, notes=None):
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
    fields.append("updated_at=?")
    values.append(datetime.now().isoformat())
    values.append(entry_id)
    conn.execute(f"UPDATE fmnp_entries SET {', '.join(fields)} WHERE id=?", values)
    conn.commit()


def delete_fmnp_entry(entry_id):
    conn = get_connection()
    conn.execute("DELETE FROM fmnp_entries WHERE id=?", (entry_id,))
    conn.commit()
