"""Vendor CRUD operations."""

from fam.database.connection import get_connection


def get_all_vendors(active_only=False):
    conn = get_connection()
    if active_only:
        rows = conn.execute("SELECT * FROM vendors WHERE is_active=1 ORDER BY name").fetchall()
    else:
        rows = conn.execute("SELECT * FROM vendors ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_vendor_by_id(vendor_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    return dict(row) if row else None


def create_vendor(name, contact_info=None):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO vendors (name, contact_info) VALUES (?, ?)",
        (name, contact_info)
    )
    conn.commit()
    return cursor.lastrowid


def get_vendors_for_market(market_id, active_only=True):
    """Get vendors assigned to a specific market."""
    conn = get_connection()
    query = """
        SELECT v.* FROM vendors v
        JOIN market_vendors mv ON mv.vendor_id = v.id
        WHERE mv.market_id = ?
    """
    if active_only:
        query += " AND v.is_active = 1"
    query += " ORDER BY v.name"
    rows = conn.execute(query, (market_id,)).fetchall()
    return [dict(r) for r in rows]


def get_market_vendor_ids(market_id):
    """Get set of vendor IDs assigned to a market."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT vendor_id FROM market_vendors WHERE market_id = ?", (market_id,)
    ).fetchall()
    return {r['vendor_id'] for r in rows}


def assign_vendor_to_market(market_id, vendor_id):
    """Assign a vendor to a market (idempotent)."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO market_vendors (market_id, vendor_id) VALUES (?, ?)",
        (market_id, vendor_id)
    )
    conn.commit()


def unassign_vendor_from_market(market_id, vendor_id):
    """Remove a vendor assignment from a market."""
    conn = get_connection()
    conn.execute(
        "DELETE FROM market_vendors WHERE market_id = ? AND vendor_id = ?",
        (market_id, vendor_id)
    )
    conn.commit()


def update_vendor(vendor_id, name=None, contact_info=None, is_active=None):
    conn = get_connection()
    fields = []
    values = []
    if name is not None:
        fields.append("name=?")
        values.append(name)
    if contact_info is not None:
        fields.append("contact_info=?")
        values.append(contact_info)
    if is_active is not None:
        fields.append("is_active=?")
        values.append(int(is_active))
    if not fields:
        return
    values.append(vendor_id)
    conn.execute(f"UPDATE vendors SET {', '.join(fields)} WHERE id=?", values)
    conn.commit()
