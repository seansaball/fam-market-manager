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

    Raises ValueError if the market day does not exist or is not open.
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

def update_transaction(txn_id, commit=True, **kwargs):
    """Update transaction fields. Supports: receipt_total, vendor_id, receipt_number,
    status, snap_reference_code, notes.

    When *commit* is False the caller is responsible for committing.
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
    if 'status' in kwargs and kwargs['status'] not in VALID_TRANSACTION_STATUSES:
        raise ValueError(
            f"Invalid transaction status '{kwargs['status']}'. "
            f"Must be one of: {', '.join(sorted(VALID_TRANSACTION_STATUSES))}"
        )
    if not fields:
        return
    values.append(txn_id)
    conn.execute(f"UPDATE transactions SET {', '.join(fields)} WHERE id=?", values)
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
        update_transaction(txn_id, commit=False, status='Confirmed',
                           confirmed_by=confirmed_by, confirmed_at=now)
        log_action('transactions', txn_id, 'CONFIRM', confirmed_by,
                   notes='Payment confirmed', commit=False)
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    logger.info("Transaction confirmed: id=%s by=%s", txn_id, confirmed_by)


def void_transaction(txn_id, voided_by="System"):
    """Void a transaction (soft delete)."""
    conn = get_connection()
    try:
        update_transaction(txn_id, commit=False, status='Voided')
        log_action('transactions', txn_id, 'VOID', voided_by,
                   notes='Transaction voided', commit=False)
        conn.commit()
    except Exception:
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


def search_transactions(market_day_id=None, vendor_id=None, status=None, fam_id_search=None):
    """Search transactions with optional filters."""
    conn = get_connection()
    query = """
        SELECT t.*, v.name as vendor_name, md.date as market_day_date,
               m.name as market_name,
               co.customer_label as customer_label
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
    query += " ORDER BY t.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# --- Payment line items ---

def save_payment_line_items(transaction_id, line_items, commit=True):
    """Save payment line items for a transaction. Replaces existing items.

    When *commit* is False the caller is responsible for committing.
    """
    conn = get_connection()
    try:
        conn.execute("DELETE FROM payment_line_items WHERE transaction_id=?", (transaction_id,))
        for item in line_items:
            conn.execute(
                """INSERT INTO payment_line_items
                   (transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot,
                    method_amount, match_amount, customer_charged, photo_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    transaction_id,
                    item['payment_method_id'],
                    item['method_name_snapshot'],
                    item['match_percent_snapshot'],
                    item['method_amount'],
                    item['match_amount'],
                    item['customer_charged'],
                    item.get('photo_path'),
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
