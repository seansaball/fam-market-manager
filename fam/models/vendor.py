"""Vendor CRUD operations.

Audit logging (v1.9.10+)
------------------------
Every mutating function in this module records an entry in
``audit_log`` so a reviewer can reconstruct who changed what and
when.  Mutations are committed atomically with the audit row.
The ``changed_by`` parameter defaults to ``'System'`` so legacy
call-sites (tests, scripted seeding) keep working — pass the
volunteer/admin name from UI call-sites.
"""

from fam.database.connection import get_connection
from fam.models.audit import log_action


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


def create_vendor(name, contact_info=None, check_payable_to=None,
                  street=None, city=None, state=None, zip_code=None,
                  ach_enabled=False, changed_by='System'):
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO vendors (name, contact_info, check_payable_to,"
            " street, city, state, zip_code, ach_enabled)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, contact_info, check_payable_to,
             street, city, state, zip_code, int(ach_enabled))
        )
        new_id = cursor.lastrowid
        # Permissively register the new vendor for every active payment
        # method.  This mirrors the v23→v24 migration's permissive backfill
        # so a freshly-added vendor is immediately usable; the coordinator
        # then tightens eligibility from Settings → Vendors → Methods if
        # the vendor's real-world rules are stricter (e.g. Food Bucks
        # produce-only).  Idempotent via INSERT OR IGNORE.
        try:
            conn.execute(
                "INSERT OR IGNORE INTO vendor_payment_methods"
                " (vendor_id, payment_method_id)"
                " SELECT ?, id FROM payment_methods WHERE is_active = 1",
                (new_id,)
            )
        except Exception:
            # Old DBs without the v24 table: silently skip — the migration
            # will populate when run.
            pass
        log_action('vendors', new_id, 'CREATE', changed_by,
                   new_value=name, commit=False,
                   notes=f"Vendor created: {name}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return new_id


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


def get_vendor_market_ids(vendor_id):
    """Get set of market IDs that a vendor is assigned to."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT market_id FROM market_vendors WHERE vendor_id = ?", (vendor_id,)
    ).fetchall()
    return {r['market_id'] for r in rows}


def assign_vendor_to_market(market_id, vendor_id, changed_by='System'):
    """Assign a vendor to a market (idempotent)."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO market_vendors "
            "(market_id, vendor_id) VALUES (?, ?)",
            (market_id, vendor_id)
        )
        # rowcount == 0 means it was already assigned — skip log to
        # keep the audit trail clean of no-op idempotent calls.
        if cur.rowcount > 0:
            log_action('market_vendors', vendor_id, 'ASSIGN', changed_by,
                       new_value=str(market_id), commit=False,
                       notes=f"Vendor {vendor_id} assigned to market {market_id}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def unassign_vendor_from_market(market_id, vendor_id, changed_by='System'):
    """Remove a vendor assignment from a market."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM market_vendors "
            "WHERE market_id = ? AND vendor_id = ?",
            (market_id, vendor_id)
        )
        if cur.rowcount > 0:
            log_action('market_vendors', vendor_id, 'UNASSIGN', changed_by,
                       old_value=str(market_id), commit=False,
                       notes=f"Vendor {vendor_id} unassigned from market {market_id}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def update_vendor(vendor_id, name=None, contact_info=None, is_active=None,
                  check_payable_to=None, street=None, city=None,
                  state=None, zip_code=None, ach_enabled=None,
                  changed_by='System'):
    conn = get_connection()
    # Snapshot old values so we can log the diff per-field.
    old_row = conn.execute(
        "SELECT name, contact_info, is_active, check_payable_to, "
        " street, city, state, zip_code, ach_enabled "
        "FROM vendors WHERE id=?", (vendor_id,)
    ).fetchone()
    old = dict(old_row) if old_row else {}

    fields = []
    values = []
    new_values = {}
    if name is not None:
        fields.append("name=?")
        values.append(name)
        new_values['name'] = name
    if contact_info is not None:
        fields.append("contact_info=?")
        values.append(contact_info)
        new_values['contact_info'] = contact_info
    if is_active is not None:
        fields.append("is_active=?")
        values.append(int(is_active))
        new_values['is_active'] = int(is_active)
    if check_payable_to is not None:
        fields.append("check_payable_to=?")
        values.append(check_payable_to)
        new_values['check_payable_to'] = check_payable_to
    if street is not None:
        fields.append("street=?")
        values.append(street)
        new_values['street'] = street
    if city is not None:
        fields.append("city=?")
        values.append(city)
        new_values['city'] = city
    if state is not None:
        fields.append("state=?")
        values.append(state)
        new_values['state'] = state
    if zip_code is not None:
        fields.append("zip_code=?")
        values.append(zip_code)
        new_values['zip_code'] = zip_code
    if ach_enabled is not None:
        fields.append("ach_enabled=?")
        values.append(int(ach_enabled))
        new_values['ach_enabled'] = int(ach_enabled)
    if not fields:
        return
    try:
        values.append(vendor_id)
        conn.execute(
            f"UPDATE vendors SET {', '.join(fields)} WHERE id=?",
            values)
        # One audit row per changed field so reviewers see exactly
        # what moved.  Only log fields whose value actually changed.
        for fname, new_val in new_values.items():
            old_val = old.get(fname)
            if old_val != new_val:
                log_action('vendors', vendor_id, 'UPDATE', changed_by,
                           field_name=fname,
                           old_value=old_val, new_value=new_val,
                           commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
