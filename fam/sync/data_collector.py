"""Collect current market day data for cloud sync.

Each function mirrors a report from reports_screen.py but filtered
to a single market_day_id for deterministic sync.
"""

import logging
from typing import Optional

from fam.database.connection import get_connection
from fam.models.market_day import get_open_market_day
from fam.utils.app_settings import get_market_code, get_device_id

logger = logging.getLogger('fam.sync.data_collector')


def collect_sync_data(market_day_id: Optional[int] = None) -> dict[str, list[dict]]:
    """Collect data for all 8 sync tabs.

    If *market_day_id* is None, uses the most recent market day
    (open or the last closed one).  Returns ``{sheet_name: rows}``.
    """
    if market_day_id is None:
        md = get_open_market_day()
        if md:
            market_day_id = md['id']
        else:
            # Find the most recently closed market day
            conn = get_connection()
            row = conn.execute(
                "SELECT id FROM market_days ORDER BY date DESC, id DESC LIMIT 1"
            ).fetchone()
            if row:
                market_day_id = row['id']
            else:
                logger.info("No market day found — nothing to sync")
                return {}

    conn = get_connection()
    mc = get_market_code() or ''
    did = get_device_id() or ''

    def _prepend_identity(rows: list[dict]) -> list[dict]:
        """Add market_code and device_id to every row."""
        return [{'market_code': mc, 'device_id': did, **r} for r in rows]

    data = {}
    try:
        data['Vendor Reimbursement'] = _prepend_identity(
            _collect_vendor_reimbursement(conn, market_day_id))
        data['FAM Match Report'] = _prepend_identity(
            _collect_fam_match(conn, market_day_id))
        data['Detailed Ledger'] = _prepend_identity(
            _collect_detailed_ledger(conn, market_day_id))
        data['Transaction Log'] = _prepend_identity(
            _collect_transaction_log(market_day_id))
        data['Activity Log'] = _prepend_identity(
            _collect_activity_log(conn, market_day_id))
        data['Geolocation'] = _prepend_identity(
            _collect_geolocation(conn, market_day_id))
        data['FMNP Entries'] = _prepend_identity(
            _collect_fmnp_entries(conn, market_day_id))
        data['Market Day Summary'] = _prepend_identity(
            _collect_market_day_summary(conn, market_day_id))
        data['Error Log'] = _prepend_identity(
            _collect_error_log())
    except Exception:
        logger.exception("Error collecting sync data for market_day %s",
                         market_day_id)
        return {}

    return data


# ── Individual collectors ────────────────────────────────────────


def _collect_vendor_reimbursement(conn, md_id: int) -> list[dict]:
    """Vendor Reimbursement — mirrors reports_screen.py L479-556."""
    where = "WHERE t.market_day_id = ? AND t.status IN ('Confirmed', 'Adjusted')"
    params = [md_id]

    vendor_rows = conn.execute(f"""
        SELECT v.name AS vendor,
               COALESCE(SUM(t.receipt_total), 0) AS gross_sales,
               GROUP_CONCAT(DISTINCT md.date) AS transaction_dates,
               GROUP_CONCAT(DISTINCT co.customer_label) AS customer_ids
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        LEFT JOIN customer_orders co ON t.customer_order_id = co.id
        {where}
        GROUP BY v.id, v.name
        ORDER BY v.name
    """, params).fetchall()

    match_rows = conn.execute(f"""
        SELECT v.name AS vendor,
               COALESCE(SUM(pl.match_amount), 0) AS fam_match,
               COALESCE(SUM(CASE WHEN pl.method_name_snapshot = 'FMNP'
                                 THEN pl.match_amount ELSE 0 END), 0) AS fmnp_match
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        {where}
        GROUP BY v.id, v.name
    """, params).fetchall()

    match_by_vendor = {
        r['vendor']: {'fam_match': r['fam_match'], 'fmnp_match': r['fmnp_match']}
        for r in match_rows
    }

    vendor_dict = {}
    for r in vendor_rows:
        vm = match_by_vendor.get(r['vendor'], {})
        vendor_dict[r['vendor']] = {
            'Vendor': r['vendor'],
            'Customer(s)': r['customer_ids'] or '',
            'Date(s)': r['transaction_dates'] or '',
            'Gross Sales': r['gross_sales'],
            'FAM Match': vm.get('fam_match', 0),
            'FMNP Match': vm.get('fmnp_match', 0),
        }

    # Merge external FMNP entries
    fmnp_rows = conn.execute("""
        SELECT v.name AS vendor,
               COALESCE(SUM(fe.amount), 0) AS fmnp_total,
               GROUP_CONCAT(DISTINCT md.date) AS fmnp_dates
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        JOIN market_days md ON fe.market_day_id = md.id
        WHERE fe.market_day_id = ? AND fe.status = 'Active'
        GROUP BY v.id, v.name
    """, [md_id]).fetchall()

    for r in fmnp_rows:
        if r['vendor'] in vendor_dict:
            vendor_dict[r['vendor']]['FMNP Match'] += r['fmnp_total']
        else:
            vendor_dict[r['vendor']] = {
                'Vendor': r['vendor'],
                'Customer(s)': '',
                'Date(s)': r['fmnp_dates'] or '',
                'Gross Sales': 0,
                'FAM Match': 0,
                'FMNP Match': r['fmnp_total'],
            }

    return sorted(vendor_dict.values(), key=lambda x: x['Vendor'])


def _collect_fam_match(conn, md_id: int) -> list[dict]:
    """FAM Match by Payment Method — mirrors reports_screen.py L595-650."""
    rows = conn.execute("""
        SELECT pl.method_name_snapshot AS method,
               SUM(pl.method_amount) AS total_allocated,
               SUM(pl.match_amount) AS total_fam_match
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        WHERE t.market_day_id = ? AND t.status IN ('Confirmed', 'Adjusted')
        GROUP BY pl.method_name_snapshot
        ORDER BY pl.method_name_snapshot
    """, [md_id]).fetchall()

    result = [
        {'Payment Method': r['method'],
         'Total Allocated': r['total_allocated'],
         'Total FAM Match': r['total_fam_match']}
        for r in rows
    ]

    # Add external FMNP total
    fmnp_total = conn.execute("""
        SELECT COALESCE(SUM(fe.amount), 0) AS total
        FROM fmnp_entries fe
        WHERE fe.market_day_id = ? AND fe.status = 'Active'
    """, [md_id]).fetchone()['total']

    if fmnp_total > 0:
        result.append({
            'Payment Method': 'FMNP (External)',
            'Total Allocated': fmnp_total,
            'Total FAM Match': fmnp_total,
        })

    return result


def _collect_detailed_ledger(conn, md_id: int) -> list[dict]:
    """Detailed Ledger — mirrors reports_screen.py L660-743."""
    from fam.utils.photo_paths import parse_photo_paths

    rows = conn.execute("""
        SELECT t.fam_transaction_id, v.name AS vendor,
               t.receipt_total, t.status,
               COALESCE(co.customer_label, '') AS customer_id,
               COALESCE(SUM(pl.customer_charged), 0) AS customer_paid,
               COALESCE(SUM(pl.match_amount), 0) AS fam_match,
               GROUP_CONCAT(pl.method_name_snapshot || ': $' ||
                   PRINTF('%.2f', pl.method_amount), ', ') AS methods
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        LEFT JOIN customer_orders co ON t.customer_order_id = co.id
        LEFT JOIN payment_line_items pl ON pl.transaction_id = t.id
        WHERE t.market_day_id = ? AND t.status != 'Draft'
        GROUP BY t.id
        ORDER BY t.fam_transaction_id
    """, [md_id]).fetchall()

    # Gather payment photo URLs per transaction
    photo_rows = conn.execute("""
        SELECT pl.transaction_id, pl.photo_drive_url
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        WHERE t.market_day_id = ?
          AND pl.photo_drive_url IS NOT NULL
          AND pl.photo_drive_url != ''
    """, [md_id]).fetchall()

    txn_photos = {}
    for pr in photo_rows:
        urls = parse_photo_paths(pr['photo_drive_url'])
        if urls:
            txn_photos.setdefault(pr['transaction_id'], []).extend(urls)

    result = []
    for r in rows:
        row_dict = {
            'Transaction ID': r['fam_transaction_id'],
            'Customer': r['customer_id'],
            'Vendor': r['vendor'],
            'Receipt Total': r['receipt_total'],
            'Customer Paid': r['customer_paid'],
            'FAM Match': r['fam_match'],
            'Status': r['status'],
            'Payment Methods': r['methods'] or '',
        }
        # Include payment photo URLs if any
        # Look up transaction id for photo mapping
        txn_row = conn.execute(
            "SELECT id FROM transactions WHERE fam_transaction_id = ?",
            [r['fam_transaction_id']]
        ).fetchone()
        if txn_row and txn_row['id'] in txn_photos:
            row_dict['Photos'] = ' | '.join(txn_photos[txn_row['id']])
        result.append(row_dict)

    # Append external FMNP entries
    fmnp_rows = conn.execute("""
        SELECT fe.id, v.name AS vendor, fe.amount, fe.check_count
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        WHERE fe.market_day_id = ? AND fe.status = 'Active'
        ORDER BY fe.id
    """, [md_id]).fetchall()

    for r in fmnp_rows:
        check_info = (f"FMNP (External) - {r['check_count']} checks"
                      if r['check_count'] else "FMNP (External)")
        result.append({
            'Transaction ID': f"FMNP-{r['id']}",
            'Customer': '',
            'Vendor': r['vendor'],
            'Receipt Total': r['amount'],
            'Customer Paid': 0,
            'FAM Match': r['amount'],
            'Status': 'FMNP Entry',
            'Payment Methods': check_info,
        })

    return result


def _collect_transaction_log(md_id: int) -> list[dict]:
    """Transaction Log — uses audit.get_transaction_log()."""
    from fam.models.audit import get_transaction_log, ACTION_LABELS

    raw = get_transaction_log(market_day_id=md_id, limit=500)
    result = []
    for r in raw:
        action_label = ACTION_LABELS.get(r['action'], r['action'])
        txn_id = r.get('fam_transaction_id') or ''
        vendor = r.get('vendor_name') or ''

        # Build details string
        parts = []
        if r.get('field_name'):
            parts.append(r['field_name'])
        if r.get('old_value') and r.get('new_value'):
            parts.append(f"{r['old_value']} → {r['new_value']}")
        elif r.get('new_value'):
            parts.append(str(r['new_value']))
        if r.get('notes'):
            parts.append(r['notes'])
        details = ' | '.join(parts) if parts else ''

        result.append({
            'Time': r['changed_at'] or '',
            'Action': action_label,
            'Transaction': txn_id,
            'Vendor': vendor,
            'Details': details,
            'By': r.get('changed_by') or '',
            'App Version': r.get('app_version') or '',
            'Device': r.get('device_id') or '',
        })

    return result


def _collect_activity_log(conn, md_id: int) -> list[dict]:
    """Activity Log — mirrors reports_screen.py L856-862.

    Scoped to entries related to the market day (by date range).
    """
    md_row = conn.execute(
        "SELECT date FROM market_days WHERE id = ?", [md_id]
    ).fetchone()
    if not md_row:
        return []

    md_date = md_row['date']
    rows = conn.execute("""
        SELECT changed_at, action, table_name, record_id,
               field_name, old_value, new_value,
               reason_code, notes, changed_by,
               app_version, device_id
        FROM audit_log
        WHERE changed_at >= ? AND changed_at < date(?, '+1 day')
        ORDER BY changed_at DESC
    """, [md_date, md_date]).fetchall()

    return [
        {'Timestamp': r['changed_at'] or '',
         'Action': r['action'] or '',
         'Table': r['table_name'] or '',
         'Record ID': r['record_id'],
         'Field': r['field_name'] or '',
         'Old Value': r['old_value'] or '',
         'New Value': r['new_value'] or '',
         'Reason': r['reason_code'] or '',
         'Notes': r['notes'] or '',
         'Changed By': r['changed_by'] or '',
         'App Version': r['app_version'] or '',
         'Device': r['device_id'] or ''}
        for r in rows
    ]


def _collect_geolocation(conn, md_id: int) -> list[dict]:
    """Geolocation — mirrors reports_screen.py L900-945."""
    rows = conn.execute("""
        SELECT co.zip_code,
               COUNT(DISTINCT co.customer_label) AS customer_count,
               COUNT(t.id) AS receipt_count,
               COALESCE(SUM(t.receipt_total), 0) AS total_spend,
               COALESCE(SUM(pli_agg.total_match), 0) AS total_match
        FROM customer_orders co
        JOIN transactions t ON t.customer_order_id = co.id
        JOIN vendors v ON t.vendor_id = v.id
        LEFT JOIN (
            SELECT transaction_id, SUM(match_amount) AS total_match
            FROM payment_line_items
            GROUP BY transaction_id
        ) pli_agg ON pli_agg.transaction_id = t.id
        WHERE t.market_day_id = ? AND t.status IN ('Confirmed', 'Adjusted')
          AND co.zip_code IS NOT NULL AND co.zip_code != ''
        GROUP BY co.zip_code
        ORDER BY customer_count DESC
    """, [md_id]).fetchall()

    return [
        {'Zip Code': r['zip_code'],
         '# Customers': r['customer_count'],
         '# Receipts': r['receipt_count'],
         'Total Spend': r['total_spend'],
         'Total FAM Match': r['total_match']}
        for r in rows
    ]


def _collect_fmnp_entries(conn, md_id: int) -> list[dict]:
    """FMNP Entries for the market day."""
    from fam.utils.photo_paths import parse_photo_paths

    rows = conn.execute("""
        SELECT fe.id, v.name AS vendor, fe.amount,
               fe.check_count, fe.notes, fe.entered_by,
               fe.photo_drive_url
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        WHERE fe.market_day_id = ? AND fe.status = 'Active'
        ORDER BY fe.id
    """, [md_id]).fetchall()

    result = []
    for r in rows:
        # Parse JSON array of Drive URLs; join with " | " for Sheets display
        urls = parse_photo_paths(r['photo_drive_url'])
        photo_display = ' | '.join(urls) if urls else ''
        result.append({
            'Entry ID': r['id'],
            'Vendor': r['vendor'],
            'Amount': r['amount'],
            'Check Count': r['check_count'] or 0,
            'Notes': r['notes'] or '',
            'Entered By': r['entered_by'] or '',
            'Photo': photo_display,
        })
    return result


def _collect_market_day_summary(conn, md_id: int) -> list[dict]:
    """Market Day Summary — new query aggregating totals."""
    row = conn.execute("""
        SELECT m.name AS market, md.date, md.status,
               md.opened_by, md.closed_by,
               COUNT(DISTINCT CASE WHEN t.status IN ('Confirmed', 'Adjusted')
                     THEN t.id END) AS txn_count,
               COALESCE(SUM(CASE WHEN t.status IN ('Confirmed', 'Adjusted')
                     THEN t.receipt_total ELSE 0 END), 0) AS total_receipts,
               COALESCE(SUM(CASE WHEN t.status IN ('Confirmed', 'Adjusted')
                     THEN pli.customer_total ELSE 0 END), 0) AS total_customer_paid,
               COALESCE(SUM(CASE WHEN t.status IN ('Confirmed', 'Adjusted')
                     THEN pli.match_total ELSE 0 END), 0) AS total_fam_match
        FROM market_days md
        JOIN markets m ON md.market_id = m.id
        LEFT JOIN transactions t ON t.market_day_id = md.id
        LEFT JOIN (
            SELECT transaction_id,
                   SUM(customer_charged) AS customer_total,
                   SUM(match_amount) AS match_total
            FROM payment_line_items
            GROUP BY transaction_id
        ) pli ON pli.transaction_id = t.id
        WHERE md.id = ?
        GROUP BY md.id
    """, [md_id]).fetchone()

    if not row:
        return []

    return [{
        'Market': row['market'],
        'Date': row['date'],
        'Status': row['status'],
        'Opened By': row['opened_by'] or '',
        'Closed By': row['closed_by'] or '',
        'Transaction Count': row['txn_count'],
        'Total Receipts': row['total_receipts'],
        'Total Customer Paid': row['total_customer_paid'],
        'Total FAM Match': row['total_fam_match'],
    }]


def _collect_error_log() -> list[dict]:
    """Error Log — parse fam_manager.log for errors and warnings.

    Syncs application errors to Google Sheets for remote troubleshooting.
    Includes full tracebacks, app version, and device identity.
    """
    from fam import __version__
    from fam.utils.logging_config import get_log_path
    from fam.utils.log_reader import parse_log_file

    log_path = get_log_path()
    entries = parse_log_file(log_path, limit=500)

    did = get_device_id() or ''

    return [
        {'Timestamp': e['timestamp'],
         'Level': e['level'],
         'Area': e['module_label'],
         'Module': e['module'],
         'Message': e['message'],
         'Traceback': (e.get('traceback') or '').strip(),
         'App Version': __version__,
         'Device': did}
        for e in entries
    ]
