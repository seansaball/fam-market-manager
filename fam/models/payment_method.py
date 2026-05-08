"""Payment method CRUD operations."""

import logging

from fam.database.connection import get_connection

logger = logging.getLogger('fam.models.payment_method')


# ── System-managed payment methods (schema v25+) ──────────────────
#
# Some payment methods are app-managed rather than coordinator-managed
# — they're inserted by migrations, locked in Settings, and only
# appear in the UI through specific code paths (never the regular
# selection dropdowns).  Currently the only system method is
# *Unallocated Funds*, auto-injected by the Adjustments dialog when a
# manager confirms the customer is gone and FAM has to absorb the
# undercharge.  Keeping the name as a constant lets call sites compare
# against it without spreading magic strings around.
UNALLOCATED_FUNDS_NAME = 'Unallocated Funds'

# v2.0.7: SNAP and Cash are universally accepted at every vendor.
# Volunteers cannot un-bind them via the Settings → Vendors → Eligible
# Payment Methods dialog — the checkboxes render checked + disabled
# in the UI, and the model layer refuses to remove the binding.  This
# defensive control eliminates the "non-denom method exceeds eligible-
# vendor capacity" error class for the most common payment instruments
# while preserving the per-vendor eligibility infrastructure (and the
# Layer 2B capacity check) for any future method that DOES have
# real-world eligibility constraints (e.g. produce-only Food Bucks).
#
# The match-by-name approach mirrors how FMNP is identified throughout
# the codebase — coordinators are explicitly warned not to rename
# these methods in Settings.
UNIVERSAL_VENDOR_METHOD_NAMES = frozenset({'SNAP', 'Cash'})


def is_universal_vendor_method(method_name: str | None) -> bool:
    """Return True if the method is universally accepted at every
    vendor and cannot be unassigned via the Vendor Eligibility UI."""
    return (method_name or '') in UNIVERSAL_VENDOR_METHOD_NAMES


def get_all_payment_methods(active_only=False, include_system=True):
    """List payment methods.

    *include_system*: when ``False``, methods flagged ``is_system=1``
    are excluded.  Selection UIs (Payment screen, Adjustments dialog
    dropdowns) pass ``include_system=False`` so coordinators cannot
    pick Unallocated Funds manually — it's only auto-injected by the
    Adjustments "customer gone" path.  Settings → Payment Methods
    keeps the default (``include_system=True``) so the system row is
    visible there for inspection (just locked from edit/delete).
    """
    conn = get_connection()
    clauses = []
    if active_only:
        clauses.append("is_active = 1")
    if not include_system:
        clauses.append("COALESCE(is_system, 0) = 0")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM payment_methods{where}"
        " ORDER BY sort_order, name"
    ).fetchall()
    return [dict(r) for r in rows]


def get_unallocated_funds_method():
    """Return the system 'Unallocated Funds' payment method row.

    The Adjustments "customer gone" path auto-injects a
    payment_line_item for this method when the manager confirms the
    customer is no longer available to pay.

    Self-healing semantics (v1.9.10+)
    ---------------------------------
    The row is normally seeded by the v24→v25 schema migration and
    is also re-seeded on every fresh-install path.  Production hit
    one case where a manager opened the Adjustments dialog and got
    "Unallocated Funds method missing — cannot record absorption"
    even though the migration had run on app start.  The most likely
    causes are:

      - A pre-v25 row at id=9999 blocked ``INSERT OR IGNORE`` from
        seeding the system row (the seed targets id=9999 explicitly
        to leave low IDs free for test fixtures).
      - The row was hand-deleted from the DB outside the app.
      - Sync/restore from an older backup overwrote it.
      - A partial migration aborted before the seed but after the
        ``schema_version`` row was advanced.

    Rather than failing the manager's adjustment with a "system
    error" the user can't recover from, we attempt to re-seed the
    row on demand here.  If the seed succeeds, return the seeded
    row; if it can't be created (truly broken DB state), return
    None and let the caller surface the original error.

    Returns ``None`` only if both the lookup AND the on-demand
    seeding fail — production should never see this.
    """
    row = get_payment_method_by_name(UNALLOCATED_FUNDS_NAME)
    if row is not None:
        return row

    # Lookup failed — attempt to re-seed.  Mirrors the v24→v25
    # migration's seed exactly so the recovered row matches what
    # the migration would have produced.
    conn = get_connection()
    try:
        # Make sure is_system column exists (pre-v25 schemas don't
        # have it; if we're here on a pre-v25 schema we still want
        # to recover gracefully).
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(payment_methods)").fetchall()}
        if 'is_system' not in cols:
            conn.execute(
                "ALTER TABLE payment_methods ADD COLUMN is_system "
                "BOOLEAN DEFAULT 0"
            )

        conn.execute(
            "INSERT OR IGNORE INTO payment_methods "
            "(id, name, match_percent, is_active, sort_order, "
            " denomination, photo_required, is_system) "
            "VALUES (9999, ?, ?, 1, 999, NULL, NULL, 1)",
            (UNALLOCATED_FUNDS_NAME, 0.0)
        )
        # If id=9999 was occupied by some other row, INSERT OR
        # IGNORE silently no-op'd.  Try again WITHOUT the explicit
        # id so SQLite picks a free one.
        check = get_payment_method_by_name(UNALLOCATED_FUNDS_NAME)
        if check is None:
            conn.execute(
                "INSERT INTO payment_methods "
                "(name, match_percent, is_active, sort_order, "
                " denomination, photo_required, is_system) "
                "VALUES (?, ?, 1, 999, NULL, NULL, 1)",
                (UNALLOCATED_FUNDS_NAME, 0.0)
            )

        # Backfill vendor eligibility so the new row passes the
        # eligibility guard during the very next adjustment save.
        pm_row = conn.execute(
            "SELECT id FROM payment_methods WHERE name = ?",
            (UNALLOCATED_FUNDS_NAME,)
        ).fetchone()
        if pm_row is not None:
            conn.execute(
                "INSERT OR IGNORE INTO vendor_payment_methods "
                "(vendor_id, payment_method_id) "
                "SELECT id, ? FROM vendors",
                (pm_row[0],)
            )
        conn.commit()
    except Exception:
        # On any failure, fall through to the original-None return —
        # the caller's error path still surfaces a friendly dialog.
        try:
            conn.rollback()
        except Exception:
            pass
        return None

    return get_payment_method_by_name(UNALLOCATED_FUNDS_NAME)


def is_system_method(method):
    """True if the row dict (from any of the get_* helpers) is a
    system-managed method.  ``is_system`` may be missing from rows
    fetched against pre-v25 schemas — treat absence as 0."""
    if not method:
        return False
    return bool(method.get('is_system') or 0)


def get_payment_method_by_id(pm_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM payment_methods WHERE id=?", (pm_id,)).fetchone()
    return dict(row) if row else None


def get_payment_method_by_name(name):
    """Look up a payment method by its exact name (case-sensitive)."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM payment_methods WHERE name=?", (name,)).fetchone()
    return dict(row) if row else None


def create_payment_method(name, match_percent, sort_order=0,
                          denomination=None, changed_by='System'):
    """Create a new payment method.  Audited (v1.9.10+).

    The ``changed_by`` parameter defaults to ``'System'`` so legacy
    callers (tests, scripted seeding) are unaffected; pass the
    volunteer/admin name from UI call-sites.
    """
    from fam.models.audit import log_action
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO payment_methods "
            "(name, match_percent, sort_order, denomination)"
            " VALUES (?, ?, ?, ?)",
            (name, match_percent, sort_order, denomination)
        )
        new_id = cursor.lastrowid
        log_action('payment_methods', new_id, 'CREATE', changed_by,
                   new_value=name, commit=False,
                   notes=(f"Payment method created: {name} "
                           f"(match {match_percent}%, "
                           f"denomination {denomination or 'none'})"))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return new_id


def get_market_payment_method_ids(market_id):
    """Get set of payment method IDs assigned to a market."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT payment_method_id FROM market_payment_methods WHERE market_id = ?",
        (market_id,)
    ).fetchall()
    return {r['payment_method_id'] for r in rows}


def get_payment_methods_for_market(market_id, active_only=True,
                                   include_system=True):
    """Get payment methods assigned to a specific market.

    *include_system*: when ``False``, system-managed methods are
    excluded — see ``get_all_payment_methods`` for the rationale.
    """
    conn = get_connection()
    query = """
        SELECT pm.* FROM payment_methods pm
        JOIN market_payment_methods mpm ON mpm.payment_method_id = pm.id
        WHERE mpm.market_id = ?
    """
    if active_only:
        query += " AND pm.is_active = 1"
    if not include_system:
        query += " AND COALESCE(pm.is_system, 0) = 0"
    query += " ORDER BY pm.sort_order, pm.name"
    rows = conn.execute(query, (market_id,)).fetchall()
    return [dict(r) for r in rows]


def assign_payment_method_to_market(market_id, payment_method_id,
                                     changed_by='System'):
    """Assign a payment method to a market (idempotent).  Audited."""
    from fam.models.audit import log_action
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO market_payment_methods "
            "(market_id, payment_method_id) VALUES (?, ?)",
            (market_id, payment_method_id)
        )
        if cur.rowcount > 0:
            log_action('market_payment_methods', payment_method_id,
                       'ASSIGN', changed_by,
                       new_value=str(market_id), commit=False,
                       notes=(f"Payment method {payment_method_id} "
                               f"assigned to market {market_id}"))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def unassign_payment_method_from_market(market_id, payment_method_id,
                                         changed_by='System'):
    """Remove a payment method assignment from a market.  Audited."""
    from fam.models.audit import log_action
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM market_payment_methods "
            "WHERE market_id = ? AND payment_method_id = ?",
            (market_id, payment_method_id)
        )
        if cur.rowcount > 0:
            log_action('market_payment_methods', payment_method_id,
                       'UNASSIGN', changed_by,
                       old_value=str(market_id), commit=False,
                       notes=(f"Payment method {payment_method_id} "
                               f"unassigned from market {market_id}"))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── Vendor-level payment-method eligibility (schema v24+) ────────
#
# Markets say "this market accepts SNAP / Cash / Food Bucks"; vendors
# (within a market) say "this vendor accepts the subset they're
# registered for".  Denominated instruments (Food Bucks, FMNP-as-
# payment) bind to a single vendor at capture time on the Payment
# screen, so the row's vendor dropdown filters by this table.

def get_vendor_payment_method_ids(vendor_id):
    """Return a set of payment_method ids this vendor is registered for."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT payment_method_id FROM vendor_payment_methods"
        " WHERE vendor_id = ?",
        (vendor_id,)
    ).fetchall()
    return {r['payment_method_id'] for r in rows}


def get_payment_methods_for_vendor(vendor_id, active_only=True):
    """Return payment methods this vendor is registered for.

    Vendor-level eligibility ONLY — most callers actually want the
    intersection with the market-level eligibility set.  Use
    ``get_eligible_vendors_for_payment_method`` from the Payment
    screen direction (filter vendors by method) instead.
    """
    conn = get_connection()
    query = """
        SELECT pm.* FROM payment_methods pm
        JOIN vendor_payment_methods vpm ON vpm.payment_method_id = pm.id
        WHERE vpm.vendor_id = ?
    """
    if active_only:
        query += " AND pm.is_active = 1"
    query += " ORDER BY pm.sort_order, pm.name"
    rows = conn.execute(query, (vendor_id,)).fetchall()
    return [dict(r) for r in rows]


def get_eligible_vendors_for_payment_method(payment_method_id, vendor_ids=None):
    """Return active vendors registered for a given payment method.

    *vendor_ids* (optional) constrains the result to a specific
    subset — typical Payment-screen usage passes the vendors on the
    current customer's order so the dropdown only shows vendors
    actually available in this transaction.
    """
    conn = get_connection()
    if vendor_ids:
        placeholders = ','.join('?' for _ in vendor_ids)
        query = (
            "SELECT v.* FROM vendors v"
            " JOIN vendor_payment_methods vpm ON vpm.vendor_id = v.id"
            f" WHERE vpm.payment_method_id = ? AND v.id IN ({placeholders})"
            " AND v.is_active = 1"
            " ORDER BY v.name"
        )
        rows = conn.execute(query, [payment_method_id, *vendor_ids]).fetchall()
    else:
        rows = conn.execute(
            "SELECT v.* FROM vendors v"
            " JOIN vendor_payment_methods vpm ON vpm.vendor_id = v.id"
            " WHERE vpm.payment_method_id = ? AND v.is_active = 1"
            " ORDER BY v.name",
            (payment_method_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def assign_payment_method_to_vendor(vendor_id, payment_method_id,
                                     changed_by='System'):
    """Register a payment method for a vendor (idempotent).  Audited."""
    from fam.models.audit import log_action
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO vendor_payment_methods"
            " (vendor_id, payment_method_id) VALUES (?, ?)",
            (vendor_id, payment_method_id)
        )
        if cur.rowcount > 0:
            log_action('vendor_payment_methods', payment_method_id,
                       'ASSIGN', changed_by,
                       new_value=str(vendor_id), commit=False,
                       notes=(f"Payment method {payment_method_id} "
                               f"registered for vendor {vendor_id}"))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def unassign_payment_method_from_vendor(vendor_id, payment_method_id,
                                         changed_by='System'):
    """Remove a payment-method registration from a vendor.  Audited.

    v2.0.7: Refuses to unassign methods in
    ``UNIVERSAL_VENDOR_METHOD_NAMES`` (SNAP, Cash).  These are
    universally accepted at every vendor by policy — preventing
    them from being un-bound eliminates the mixed-eligibility
    overflow problem class for the most common non-denom methods.
    The defensive guard fires regardless of UI state (a misclick,
    a .fam import, or a direct model call all hit the same gate).
    Returns silently when the method is universal — caller code
    typically iterates over a "to remove" set and we don't want
    to raise on every iteration.
    """
    from fam.models.audit import log_action

    pm = get_payment_method_by_id(payment_method_id)
    if pm and is_universal_vendor_method(pm['name']):
        logger.warning(
            "Refused to unassign universal vendor method '%s' "
            "from vendor=%s — SNAP and Cash are bound at every "
            "vendor by policy (v2.0.7).  No-op.",
            pm['name'], vendor_id,
        )
        return

    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM vendor_payment_methods"
            " WHERE vendor_id = ? AND payment_method_id = ?",
            (vendor_id, payment_method_id)
        )
        if cur.rowcount > 0:
            log_action('vendor_payment_methods', payment_method_id,
                       'UNASSIGN', changed_by,
                       old_value=str(vendor_id), commit=False,
                       notes=(f"Payment method {payment_method_id} "
                               f"removed from vendor {vendor_id}"))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def update_payment_method(pm_id, name=None, match_percent=None,
                          is_active=None, sort_order=None,
                          denomination=None, photo_required=None,
                          changed_by='System'):
    """Update a payment method.  Audited per-changed-field (v1.9.10+).

    The ``changed_by`` parameter defaults to ``'System'`` for
    backward compatibility with tests; pass the volunteer/admin
    name from UI call-sites.
    """
    from fam.models.audit import log_action
    conn = get_connection()
    # Snapshot old values for the per-field diff log.
    old_row = conn.execute(
        "SELECT name, match_percent, is_active, sort_order, "
        " denomination, photo_required FROM payment_methods "
        "WHERE id=?", (pm_id,)
    ).fetchone()
    old = dict(old_row) if old_row else {}

    fields = []
    values = []
    new_values = {}
    if name is not None:
        fields.append("name=?")
        values.append(name)
        new_values['name'] = name
    if match_percent is not None:
        fields.append("match_percent=?")
        values.append(match_percent)
        new_values['match_percent'] = match_percent
    if is_active is not None:
        fields.append("is_active=?")
        values.append(int(is_active))
        new_values['is_active'] = int(is_active)
    if sort_order is not None:
        fields.append("sort_order=?")
        values.append(sort_order)
        new_values['sort_order'] = sort_order
    if denomination is not None:
        # 0 means "clear denomination" (set to NULL); positive = set value
        v = None if denomination == 0 else denomination
        fields.append("denomination=?")
        values.append(v)
        new_values['denomination'] = v
    if photo_required is not None:
        # 'Off' means "clear" (set to NULL); 'Optional'/'Mandatory' stored as-is
        v = None if photo_required == 'Off' else photo_required
        fields.append("photo_required=?")
        values.append(v)
        new_values['photo_required'] = v
    if not fields:
        return
    try:
        values.append(pm_id)
        conn.execute(
            f"UPDATE payment_methods SET {', '.join(fields)} "
            "WHERE id=?", values)
        for fname, new_val in new_values.items():
            old_val = old.get(fname)
            if old_val != new_val:
                log_action('payment_methods', pm_id, 'UPDATE',
                           changed_by,
                           field_name=fname,
                           old_value=old_val, new_value=new_val,
                           commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
