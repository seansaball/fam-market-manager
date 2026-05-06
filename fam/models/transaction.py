"""Transaction and payment line item CRUD operations."""

import logging
from fam.database.connection import get_connection
from fam.utils.timezone import eastern_timestamp
from fam.models.audit import log_action

logger = logging.getLogger('fam.models.transaction')


def generate_transaction_id(market_day_date: str) -> str:
    """Generate a unique FAM-{CODE}-{DEV}-YYYYMMDD-NNNN transaction ID.

    When a market code **and** device ID are configured the ID includes
    both (e.g. ``FAM-DT-0c2a-20260306-0001``).  The 4-char device tag
    ensures that two workstations at the same market on the same day
    never produce colliding IDs.

    Falls back gracefully when either is missing:
    - ``FAM-{CODE}-YYYYMMDD-NNNN`` (no device ID)
    - ``FAM-YYYYMMDD-NNNN``        (no market code or device ID)

    Sequence numbering checks the current prefix first, then older
    formats for backward compatibility.
    """
    from fam.utils.app_settings import get_market_code, get_device_id
    conn = get_connection()
    date_part = market_day_date.replace("-", "")

    market_code = get_market_code()
    device_id = get_device_id()
    dev_tag = device_id[:4] if device_id else ''

    if market_code and dev_tag:
        prefix = f"FAM-{market_code}-{dev_tag}-{date_part}-"
    elif market_code:
        prefix = f"FAM-{market_code}-{date_part}-"
    else:
        prefix = f"FAM-{date_part}-"

    # Check current-format IDs first
    row = conn.execute(
        "SELECT fam_transaction_id FROM transactions "
        "WHERE fam_transaction_id LIKE ? "
        "ORDER BY fam_transaction_id DESC LIMIT 1",
        (prefix + "%",)
    ).fetchone()

    if row:
        last_seq = int(row[0].split("-")[-1])
        next_seq = last_seq + 1
    else:
        # Check older formats for sequence continuity
        fallback_prefixes = []
        if market_code:
            fallback_prefixes.append(f"FAM-{market_code}-{date_part}-")
        fallback_prefixes.append(f"FAM-{date_part}-")

        next_seq = 1
        for fb_prefix in fallback_prefixes:
            if fb_prefix == prefix:
                continue
            row = conn.execute(
                "SELECT fam_transaction_id FROM transactions "
                "WHERE fam_transaction_id LIKE ? "
                "ORDER BY fam_transaction_id DESC LIMIT 1",
                (fb_prefix + "%",)
            ).fetchone()
            if row:
                last_seq = int(row[0].split("-")[-1])
                next_seq = last_seq + 1
                break

    return f"{prefix}{next_seq:04d}"


def create_transaction(market_day_id, vendor_id, receipt_total, receipt_number=None,
                       market_day_date=None, notes=None, customer_order_id=None):
    """Create a new draft transaction. Returns (transaction_id, fam_transaction_id).

    receipt_total is in integer cents (e.g. 8999 for $89.99).

    Raises ValueError when:
      * the market day does not exist
      * the market day is not Open
      * the market day's date is *before* today (a "stale" open
        market day — the volunteer crossed midnight without closing
        the prior day, so writing a transaction here would mis-date
        it to yesterday).  v1.9.9+ guard.
    """
    conn = get_connection()

    row = conn.execute(
        "SELECT date, status FROM market_days WHERE id=?", (market_day_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Market day {market_day_id} not found")
    if row['status'] != 'Open':
        raise ValueError(
            f"Market day {market_day_id} is '{row['status']}' — "
            f"transactions can only be created on an open market day"
        )

    # v1.9.9+ stale-date guard.  Even if the market day is still
    # marked Open, refuse to write a transaction whose attributed
    # date is in the past — those transactions would otherwise show
    # up under yesterday's market in every report.
    from fam.utils.timezone import eastern_today
    today_iso = eastern_today().isoformat()
    if row['date'] < today_iso:
        raise ValueError(
            f"Market day {market_day_id} has date {row['date']} "
            f"(today is {today_iso}).  Close this market day and "
            f"open a new one for today before recording transactions."
        )

    if market_day_date is None:
        market_day_date = row['date']

    fam_tid = generate_transaction_id(market_day_date)

    cursor = conn.execute(
        """INSERT INTO transactions (fam_transaction_id, market_day_id, vendor_id,
           receipt_total, receipt_number, notes, customer_order_id, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'Draft', ?)""",
        (fam_tid, market_day_id, vendor_id, receipt_total, receipt_number, notes,
         customer_order_id, eastern_timestamp())
    )
    conn.commit()
    txn_id = cursor.lastrowid

    log_action('transactions', txn_id, 'CREATE', 'System',
               notes=f"Created {fam_tid} total=${receipt_total / 100:.2f} vendor={vendor_id}")
    logger.info("Transaction created: %s id=%s total=$%.2f", fam_tid, txn_id, receipt_total / 100)
    return txn_id, fam_tid


def get_transaction_by_id(txn_id):
    conn = get_connection()
    row = conn.execute("""
        SELECT t.*, v.name as vendor_name, md.date as market_day_date,
               md.market_id, m.name as market_name
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


# Valid status values for transactions
VALID_TRANSACTION_STATUSES = {'Draft', 'Confirmed', 'Adjusted', 'Voided'}

# Transitions OUT OF a status that are explicitly forbidden.
# v1.9.10 hardening (Finding H-2): voided transactions are
# **terminal**.  Allowing Voided -> Confirmed/Adjusted/Draft would
# silently restore the transaction's full financial impact (vendor
# reimbursement, FAM match, prior-match cap) without a clear audit-
# trail action code.  If reanimation is ever genuinely needed, add
# a dedicated ``unvoid_transaction(txn_id, ...)`` with its own
# 'UNVOIDED' audit action so reviewers see exactly what happened.
_FORBIDDEN_STATUS_TRANSITIONS = {
    'Voided': frozenset({'Draft', 'Confirmed', 'Adjusted'}),
}


def update_transaction(txn_id, commit=True, *, changed_by: str = 'System',
                       _skip_audit: bool = False, **kwargs):
    """Update transaction fields. Supports: receipt_total, vendor_id, receipt_number,
    status, snap_reference_code, notes.

    When *commit* is False the caller is responsible for committing.

    **Status transition guard (v1.9.10 hardening):** voided
    transactions are terminal — any transition Voided -> non-Voided
    raises ``ValueError`` to prevent silent resurrection.
    Voided -> Voided is permitted (idempotent no-op).

    **Audit trail (v1.9.10 follow-up, 2026-05-01):** every UPDATE
    writes one ``audit_log`` row per actually-changed field
    (UPDATE / CONFIRM / VOID action codes).  Callers may pass
    ``_skip_audit=True`` to suppress (used by ``confirm_transaction``
    and ``void_transaction`` which write their own structured
    rows).  ``changed_by`` should be the human-readable operator
    when known.
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
    # Validate status if being updated
    new_status = kwargs.get('status')
    if new_status is not None and new_status not in VALID_TRANSACTION_STATUSES:
        raise ValueError(
            f"Invalid transaction status '{new_status}'. "
            f"Must be one of: {', '.join(sorted(VALID_TRANSACTION_STATUSES))}"
        )

    # Status-transition guard: refuse to bring a Voided txn back.
    if new_status is not None:
        cur = conn.execute(
            "SELECT status FROM transactions WHERE id=?", (txn_id,)
        ).fetchone()
        if cur is not None:
            old_status = cur['status'] if hasattr(cur, 'keys') else cur[0]
            forbidden = _FORBIDDEN_STATUS_TRANSITIONS.get(old_status, frozenset())
            if new_status in forbidden:
                raise ValueError(
                    f"Status transition {old_status} -> {new_status} "
                    "is not permitted; voided transactions are terminal "
                    "in v1.9.10+.  If reanimation is required, expose "
                    "an explicit unvoid_transaction() with its own "
                    "audit action code."
                )

    # Vendor-eligibility guard (v1.9.10, Finding E1): when
    # re-attributing a transaction to a different vendor, refuse if
    # the new vendor is not registered for ALL payment methods on
    # the existing line items (vendor_payment_methods junction).
    # The UI's confirm-time Layer 2B guard already catches this on
    # the PaymentScreen and AdjustmentDialog paths; this is the
    # belt-and-suspenders model-layer enforcement so direct
    # update_transaction calls (scripts, future flows) cannot
    # silently land an ineligible attribution.
    new_vendor = kwargs.get('vendor_id')
    if new_vendor is not None:
        existing_method_ids = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT payment_method_id "
                "FROM payment_line_items WHERE transaction_id=?",
                (txn_id,)).fetchall()
        }
        if existing_method_ids:
            eligible_method_ids = {
                r[0] for r in conn.execute(
                    "SELECT payment_method_id "
                    "FROM vendor_payment_methods WHERE vendor_id=?",
                    (new_vendor,)).fetchall()
            }
            # Permissive fallback: if the new vendor has NO
            # vendor_payment_methods rows configured at all,
            # treat as "unrestricted" (matches v24's permissive
            # backfill semantics so legacy data doesn't break).
            if eligible_method_ids:
                ineligible = existing_method_ids - eligible_method_ids
                if ineligible:
                    raise ValueError(
                        f"Vendor {new_vendor} does not accept all "
                        f"payment methods on this transaction.  "
                        f"Ineligible payment_method_ids: "
                        f"{sorted(ineligible)}.  Either pick a "
                        f"different vendor, or update Settings -> "
                        f"Vendors -> Methods first."
                    )

    if not fields:
        return

    # Snapshot the pre-update row so we can emit per-field audit
    # entries for the values that actually changed.
    if not _skip_audit:
        before_row = conn.execute(
            "SELECT * FROM transactions WHERE id=?", (txn_id,)
        ).fetchone()
        before = dict(before_row) if before_row else {}
    else:
        before = {}

    values.append(txn_id)
    conn.execute(f"UPDATE transactions SET {', '.join(fields)} WHERE id=?", values)

    if not _skip_audit and before:
        from fam.models.audit import log_action
        for key, value in kwargs.items():
            if key not in allowed:
                continue
            old_val = before.get(key)
            if old_val == value:
                continue
            log_action('transactions', txn_id, 'UPDATE', changed_by,
                       field_name=key,
                       old_value=old_val, new_value=value,
                       commit=False)

    if commit:
        conn.commit()


def confirm_transaction(txn_id, confirmed_by="Volunteer", commit=True):
    """Set transaction status to Confirmed.

    Both the status update and audit log entry are written atomically.
    When *commit* is False the caller is responsible for committing.
    """
    conn = get_connection()
    now = eastern_timestamp()
    try:
        # Skip update_transaction's per-field audit — we emit a
        # single CONFIRM action below instead of three UPDATE rows
        # for status/confirmed_by/confirmed_at.
        update_transaction(txn_id, commit=False, status='Confirmed',
                           confirmed_by=confirmed_by, confirmed_at=now,
                           _skip_audit=True)
        log_action('transactions', txn_id, 'CONFIRM', confirmed_by,
                   notes='Payment confirmed', commit=False)
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    logger.info("Transaction confirmed: id=%s by=%s", txn_id, confirmed_by)


def void_transaction(txn_id, voided_by="System", commit=True):
    """Void a transaction (soft delete).

    v2.0.2 fix (UF-H1/H2): added ``commit`` parameter so callers can
    bundle this void with subsequent writes (e.g. flipping a parent
    customer_order to Voided when the last child txn is voided)
    in a single atomic transaction.  Pre-fix the model committed
    here unconditionally — a transient ``database is locked`` on
    the order-status flip would leave the txn voided but the order
    still Confirmed/Draft, breaking reports/audit-trail invariants.

    v2.0.6 fix (2026-05-06, user-reported): also drop any
    ``photo_hashes`` cache rows that pointed to Drive URLs unique
    to this transaction.  Without this cleanup, re-uploading the
    same receipt image to a fresh transaction short-circuits to
    the VOIDed Drive file (which ``_process_voided_photos``
    renames to ``VOID_*`` on the next sync) rather than triggering
    a fresh upload.  Photos still shared by another active
    transaction stay cached so dedup keeps working for them.
    """
    from fam.models.photo_hash import (
        cleanup_orphaned_hashes_for_transaction)

    conn = get_connection()
    try:
        # Skip update_transaction's per-field audit — we emit a
        # single VOID action below instead of an UPDATE row.
        update_transaction(txn_id, commit=False, status='Voided',
                           _skip_audit=True)
        log_action('transactions', txn_id, 'VOID', voided_by,
                   notes='Transaction voided', commit=False)
        # Hash cleanup runs AFTER the status flip so the "active
        # references" query inside the helper correctly excludes
        # this just-voided transaction.  Don't commit inside the
        # helper — the bundle commits below.
        cleanup_orphaned_hashes_for_transaction(
            txn_id, commit=False)
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    logger.info("Transaction voided: id=%s by=%s", txn_id, voided_by)


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


def search_transactions(market_day_id=None, vendor_id=None, status=None,
                        fam_id_search=None, date_from=None, date_to=None):
    """Search transactions with optional filters.

    The result set carries a derived ``last_updated`` field — the
    most recent audit_log entry for this transaction (across the
    transactions table itself AND the payment_line_items audits,
    since both record_id back to the transaction), falling back to
    ``created_at`` when nothing has been audited yet.

    *date_from* / *date_to*: optional 'yyyy-MM-dd' strings filtering on
    that ``last_updated`` field (inclusive on both ends).  This
    matches the Adjustments-page mental model: "show me transactions
    I worked on today / this week" — i.e. anything created or
    modified in the window.  An adjustment to a 6-month-old
    transaction surfaces in today's filter window the same as a
    fresh entry, which is what the coordinator expects when they
    open the screen to review their session's work.

    NB: This is a deliberately DIFFERENT semantic from the Reports
    screen, which filters by ``md.date`` (the market day's business
    date) because reports aggregate revenue by the market day the
    money belongs to.  Two different mental models for two different
    workflows.
    """
    # Subquery that produces the most recent audit timestamp for a
    # given transaction id (covers both 'transactions' and
    # 'payment_line_items' rows because audit log entries for both
    # use the transaction's id as record_id).
    last_updated_expr = """
        COALESCE(
            (SELECT MAX(al.changed_at)
               FROM audit_log al
              WHERE al.record_id = t.id
                AND al.table_name IN ('transactions',
                                       'payment_line_items')),
            t.created_at
        )
    """

    conn = get_connection()
    query = f"""
        SELECT t.*, v.name as vendor_name, md.date as market_day_date,
               m.name as market_name,
               co.customer_label as customer_label,
               {last_updated_expr} AS last_updated
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
    # Filter on DATE(last_updated) so the date-only strings the UI
    # passes (yyyy-MM-dd) compare cleanly against the timestamp.
    # The expression has to be repeated here — SQLite doesn't allow
    # SELECT-list aliases in WHERE clauses.
    if date_from:
        query += f" AND DATE({last_updated_expr}) >= ?"
        params.append(date_from)
    if date_to:
        query += f" AND DATE({last_updated_expr}) <= ?"
        params.append(date_to)
    query += " ORDER BY t.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# --- Payment line items ---

def save_payment_line_items(transaction_id, line_items, commit=True):
    """Save payment line items for a transaction. Replaces existing items.

    When *commit* is False the caller is responsible for committing.

    v1.9.10 follow-up (2026-05-01): preserve ``photo_drive_url`` on
    re-save.  Without this, every Save Draft / Confirm / Adjust
    cycle dropped the cloud-photo URL produced by the previous
    sync — the next sync would re-upload the same photo and
    payment_line_items.photo_drive_url would only ever be populated
    until the next mutation.  Now, before DELETE+INSERT, we snapshot
    the existing rows' ``(payment_method_id, photo_path) →
    photo_drive_url`` map and re-attach matching URLs to the
    incoming items.  Caller may also explicitly supply
    ``photo_drive_url`` on an item — that takes precedence.
    """
    from fam.utils.photo_paths import parse_photo_paths
    conn = get_connection()
    try:
        # Snapshot existing drive URLs keyed by (pm_id, normalized
        # photo_path) so we can preserve them across the DELETE+INSERT.
        existing_urls: dict[tuple, str] = {}
        for r in conn.execute(
                "SELECT payment_method_id, photo_path, photo_drive_url "
                "FROM payment_line_items WHERE transaction_id=?",
                (transaction_id,)):
            url = r['photo_drive_url']
            if not url:
                continue
            existing_urls[(r['payment_method_id'], r['photo_path'])] = url

        conn.execute("DELETE FROM payment_line_items WHERE transaction_id=?", (transaction_id,))
        for item in line_items:
            drive_url = item.get('photo_drive_url')
            if not drive_url:
                # Re-attach the drive URL from the prior save when
                # the same (pm_id, photo_path) pair existed.  If
                # photo_path changed (user re-took the photo) we
                # let the URL drop intentionally — it points to a
                # stale image.
                drive_url = existing_urls.get(
                    (item['payment_method_id'], item.get('photo_path')))
            conn.execute(
                """INSERT INTO payment_line_items
                   (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
                    method_amount, match_amount, customer_charged, photo_path, photo_drive_url, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    transaction_id,
                    item['payment_method_id'],
                    item['method_name_snapshot'],
                    item['match_percent_snapshot'],
                    item['method_amount'],
                    item['match_amount'],
                    item['customer_charged'],
                    item.get('photo_path'),
                    drive_url,
                    eastern_timestamp(),
                )
            )
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise

    methods_summary = ", ".join(
        f"{it['method_name_snapshot']}=${it['method_amount'] / 100:.2f}" for it in line_items
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


def get_pending_payment_photo_uploads():
    """Return payment line items that have local photo(s) with incomplete Drive uploads.

    An item is "pending" when:
      - photo_path is set (one or more local photos)
      - photo_drive_url is NULL/empty, OR has fewer URLs than photo_path
    """
    from fam.utils.photo_paths import parse_photo_paths

    conn = get_connection()
    rows = conn.execute("""
        SELECT pl.id, pl.photo_path, pl.photo_drive_url,
               pl.method_name_snapshot,
               t.fam_transaction_id,
               v.name as vendor_name,
               md.date as market_day_date,
               m.name as market_name
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        WHERE pl.photo_path IS NOT NULL
          AND pl.photo_path != ''
    """).fetchall()

    pending = []
    for r in rows:
        local_paths = parse_photo_paths(r['photo_path'])
        drive_urls = parse_photo_paths(r['photo_drive_url'])
        if len(drive_urls) < len(local_paths):
            pending.append(dict(r))
    return pending


def get_payment_items_with_drive_urls():
    """Return payment line items that have Drive URLs set (for verification)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT pl.id, pl.photo_path, pl.photo_drive_url
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        WHERE pl.photo_drive_url IS NOT NULL
          AND pl.photo_drive_url != ''
          AND t.status != 'Voided'
    """).fetchall()
    return [dict(r) for r in rows]


def get_voided_payment_photos():
    """Return payment line items for voided transactions that have Drive URLs (for VOID rename)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT pl.id, pl.photo_drive_url
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        WHERE t.status = 'Voided'
          AND pl.photo_drive_url IS NOT NULL
          AND pl.photo_drive_url != ''
    """).fetchall()
    return [dict(r) for r in rows]


def update_payment_photo_drive_url(line_item_id, drive_url):
    """Store the Google Drive URL(s) after successful photo upload.

    *drive_url* can be a single URL string or a JSON-encoded array of URLs
    (for multi-photo line items).
    """
    conn = get_connection()
    conn.execute(
        "UPDATE payment_line_items SET photo_drive_url=? WHERE id=?",
        (drive_url, line_item_id)
    )
    conn.commit()
