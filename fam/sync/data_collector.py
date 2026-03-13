"""Collect current market day data for cloud sync.

Each function mirrors a report from reports_screen.py but filtered
to a single market_day_id for deterministic sync.
"""

import logging
from datetime import datetime
from typing import Optional

from fam.database.connection import get_connection
from fam.models.market_day import get_open_market_day
from fam.utils.app_settings import (
    get_market_code, get_device_id, derive_market_code,
    is_sync_tab_enabled,
)

logger = logging.getLogger('fam.sync.data_collector')


def collect_sync_data(market_day_id: Optional[int] = None) -> dict[str, list[dict]]:
    """Collect data for all sync tabs.

    If *market_day_id* is None, collects data from ALL market days
    on this device so the full history appears in Google Sheets.
    Returns ``{sheet_name: rows}``.
    """
    conn = get_connection()
    did = get_device_id() or ''

    # Determine which market days to sync
    if market_day_id is not None:
        md_ids = [market_day_id]
    else:
        md_rows = conn.execute(
            "SELECT id FROM market_days ORDER BY date"
        ).fetchall()
        md_ids = [r['id'] for r in md_rows]
        if not md_ids:
            logger.info("No market day found — nothing to sync")
            return {}

    # Resolve the correct market_code per market day from its parent market
    md_market_codes: dict[int, str] = {}
    for md_id_val in md_ids:
        mkt_row = conn.execute("""
            SELECT m.name FROM market_days md
            JOIN markets m ON md.market_id = m.id
            WHERE md.id = ?
        """, [md_id_val]).fetchone()
        if mkt_row:
            md_market_codes[md_id_val] = derive_market_code(mkt_row['name'])
        else:
            logger.warning("Market not found for market_day %s; "
                           "using fallback market_code", md_id_val)
            md_market_codes[md_id_val] = get_market_code() or ''

    def _append_identity(rows: list[dict], mc: str) -> list[dict]:
        """Add market_code and device_id to end of every row."""
        return [{**r, 'market_code': mc, 'device_id': did} for r in rows]

    # Per-market-day collectors — run once per market day, combine rows
    per_md_collectors = [
        ('FAM Match Report',     lambda c, mid: _collect_fam_match(c, mid)),
        ('Detailed Ledger',      lambda c, mid: _collect_detailed_ledger(c, mid)),
        ('Transaction Log',      lambda c, mid: _collect_transaction_log(mid)),
        ('Activity Log',         lambda c, mid: _collect_activity_log(c, mid)),
        ('Geolocation',          lambda c, mid: _collect_geolocation(c, mid)),
        ('FMNP Entries',         lambda c, mid: _collect_fmnp_entries(c, mid)),
        ('Market Day Summary',   lambda c, mid: _collect_market_day_summary(c, mid)),
    ]

    data: dict[str, list[dict]] = {}
    for sheet_name, collector_fn in per_md_collectors:
        if not is_sync_tab_enabled(sheet_name):
            continue
        combined: list[dict] = []
        for md_id in md_ids:
            mc = md_market_codes.get(md_id, '')
            try:
                rows = collector_fn(conn, md_id)
                combined.extend(_append_identity(rows, mc))
            except Exception:
                logger.exception("Error collecting '%s' for market_day %s",
                                 sheet_name, md_id)
        data[sheet_name] = combined

    # Whole-dataset collectors — run once across all market days
    if is_sync_tab_enabled('Vendor Reimbursement') and md_ids:
        try:
            vr_rows = _collect_vendor_reimbursement(conn, md_ids)
            vr_with_identity = []
            for row in vr_rows:
                mc = derive_market_code(row['Market Name']) if row['Market Name'] else (get_market_code() or '')
                vr_with_identity.append({**row, 'market_code': mc, 'device_id': did})
            data['Vendor Reimbursement'] = vr_with_identity
        except Exception:
            logger.exception("Error collecting 'Vendor Reimbursement'")

    # Global collectors — run once (not per market day)
    # Use current market code for non-market-day-scoped data
    global_mc = get_market_code() or ''
    try:
        data['Error Log'] = _append_identity(_collect_error_log(), global_mc)
    except Exception:
        logger.exception("Error collecting 'Error Log'")

    return data


# ── Individual collectors ────────────────────────────────────────


def _build_vendor_address(row) -> str:
    """Build a single-line address from vendor street/city/state/zip fields."""
    parts = []
    street = row['street'] if row['street'] else ''
    city = row['city'] if row['city'] else ''
    state = row['state'] if row['state'] else ''
    zip_code = row['zip_code'] if row['zip_code'] else ''
    if street:
        parts.append(street)
    city_state_zip = ', '.join(filter(None, [city, state]))
    if zip_code:
        city_state_zip = f"{city_state_zip} {zip_code}".strip()
    if city_state_zip:
        parts.append(city_state_zip)
    return ', '.join(parts)


def _collect_vendor_reimbursement(conn, md_ids: list[int]) -> list[dict]:
    """Vendor Reimbursement — one row per unique (market, vendor) pair.

    Layout: Market Name, Vendor, Month, Date(s), Total Due to Vendor,
    [one column per payment method], FMNP (External), Check Payable To,
    Address.
    """
    placeholders = ','.join('?' for _ in md_ids)
    where = f"WHERE t.market_day_id IN ({placeholders}) AND t.status IN ('Confirmed', 'Adjusted')"
    params = list(md_ids)

    # Check if v19 vendor columns exist (handles un-migrated databases)
    try:
        conn.execute("SELECT check_payable_to FROM vendors LIMIT 1")
        cpt_expr = "COALESCE(v.check_payable_to, v.name)"
        addr_cols = ", v.street, v.city, v.state, v.zip_code"
    except Exception:
        cpt_expr = "v.name"
        addr_cols = ", NULL AS street, NULL AS city, NULL AS state, NULL AS zip_code"

    vendor_rows = conn.execute(f"""
        SELECT v.name AS vendor,
               {cpt_expr} AS check_payable_to,
               m.name AS market_name,
               COALESCE(SUM(t.receipt_total), 0) AS gross_sales,
               GROUP_CONCAT(DISTINCT md.date) AS transaction_dates
               {addr_cols}
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        {where}
        GROUP BY m.id, v.id, v.name
        ORDER BY m.name, v.name
    """, params).fetchall()

    # Dynamic payment method breakdown per (market, vendor)
    method_rows = conn.execute(f"""
        SELECT v.name AS vendor,
               m.name AS market_name,
               pl.method_name_snapshot AS method,
               COALESCE(SUM(pl.method_amount), 0) AS method_total
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        {where}
        GROUP BY m.id, v.id, v.name, pl.method_name_snapshot
    """, params).fetchall()

    all_methods = sorted({r['method'] for r in method_rows})
    method_by_vendor: dict[tuple, dict[str, float]] = {}
    for r in method_rows:
        key = (r['market_name'], r['vendor'])
        method_by_vendor.setdefault(key, {})[r['method']] = r['method_total']

    vendor_dict = {}
    for r in vendor_rows:
        key = (r['market_name'], r['vendor'])
        month_str = ''
        if r['transaction_dates']:
            month_str = datetime.strptime(r['transaction_dates'][:7], '%Y-%m').strftime('%B')
        row = {
            'Market Name': r['market_name'],
            'Vendor': r['vendor'],
            'Month': month_str,
            'Date(s)': r['transaction_dates'] or '',
            'Total Due to Vendor': r['gross_sales'],
        }
        vendor_methods = method_by_vendor.get(key, {})
        for m in all_methods:
            row[m] = vendor_methods.get(m, 0)
        row['FMNP (External)'] = 0
        row['Check Payable To'] = r['check_payable_to']
        row['Address'] = _build_vendor_address(r)
        vendor_dict[key] = row

    # Merge external FMNP entries
    fmnp_where = f"WHERE fe.market_day_id IN ({placeholders}) AND fe.status = 'Active'"
    fmnp_rows = conn.execute(f"""
        SELECT v.name AS vendor,
               m.name AS market_name,
               {cpt_expr} AS check_payable_to,
               COALESCE(SUM(fe.amount), 0) AS fmnp_total,
               GROUP_CONCAT(DISTINCT md.date) AS fmnp_dates
               {addr_cols}
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        JOIN market_days md ON fe.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        {fmnp_where}
        GROUP BY m.id, v.id, v.name
    """, params).fetchall()

    for r in fmnp_rows:
        key = (r['market_name'], r['vendor'])
        if key in vendor_dict:
            vendor_dict[key]['FMNP (External)'] = r['fmnp_total']
            vendor_dict[key]['Total Due to Vendor'] += r['fmnp_total']
            existing = set(vendor_dict[key]['Date(s)'].split(',')) \
                if vendor_dict[key]['Date(s)'] else set()
            new_dates = set((r['fmnp_dates'] or '').split(','))
            all_dates = (existing | new_dates) - {''}
            vendor_dict[key]['Date(s)'] = ','.join(sorted(all_dates))
        else:
            month_str = ''
            if r['fmnp_dates']:
                month_str = datetime.strptime(r['fmnp_dates'][:7], '%Y-%m').strftime('%B')
            row = {
                'Market Name': r['market_name'],
                'Vendor': r['vendor'],
                'Month': month_str,
                'Date(s)': r['fmnp_dates'] or '',
                'Total Due to Vendor': r['fmnp_total'],
            }
            for m in all_methods:
                row[m] = 0
            row['FMNP (External)'] = r['fmnp_total']
            row['Check Payable To'] = r['check_payable_to']
            row['Address'] = _build_vendor_address(r)
            vendor_dict[key] = row

    return sorted(vendor_dict.values(), key=lambda x: (x['Market Name'], x['Vendor']))


def _collect_fam_match(conn, md_id: int) -> list[dict]:
    """FAM Match by Payment Method — one row per method per market day."""
    md_row = conn.execute(
        "SELECT date FROM market_days WHERE id = ?", [md_id]
    ).fetchone()
    md_date = md_row['date'] if md_row else ''

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
         'Date': md_date,
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
            'Date': md_date,
            'Total Allocated': fmnp_total,
            'Total FAM Match': fmnp_total,
        })

    return result


def _collect_detailed_ledger(conn, md_id: int) -> list[dict]:
    """Detailed Ledger — mirrors reports_screen.py L660-743."""
    from fam.utils.photo_paths import parse_photo_paths

    rows = conn.execute("""
        SELECT t.fam_transaction_id, v.name AS vendor,
               t.receipt_total, t.status, t.created_at,
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
            'Timestamp': r['created_at'] or '',
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
        SELECT fe.id, v.name AS vendor, fe.amount, fe.check_count,
               fe.created_at
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
            'Timestamp': r['created_at'] or '',
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
         'App Version': r['app_version'] or ''}
        for r in rows
    ]


def _collect_geolocation(conn, md_id: int) -> list[dict]:
    """Geolocation — one row per zip code per market day."""
    md_row = conn.execute(
        "SELECT date FROM market_days WHERE id = ?", [md_id]
    ).fetchone()
    md_date = md_row['date'] if md_row else ''

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
         'Date': md_date,
         '# Customers': r['customer_count'],
         '# Receipts': r['receipt_count'],
         'Total Spend': r['total_spend'],
         'Total FAM Match': r['total_match']}
        for r in rows
    ]


def _collect_fmnp_entries(conn, md_id: int) -> list[dict]:
    """FMNP Entries — one row per check from both the dedicated FMNP Entry
    tab *and* FMNP collected through the normal Payment flow.
    """
    from fam.utils.photo_paths import parse_photo_paths

    # Look up FMNP denomination for per-check amount calculation
    try:
        from fam.models.payment_method import get_payment_method_by_name
        fmnp_method = get_payment_method_by_name('FMNP')
        denomination = (fmnp_method or {}).get('denomination') or 0
    except Exception:
        denomination = 0

    result = []

    # ── Source A: fmnp_entries table (dedicated FMNP Entry tab) ──
    fe_rows = conn.execute("""
        SELECT fe.id, v.name AS vendor, fe.amount,
               fe.check_count, fe.notes, fe.entered_by,
               fe.photo_drive_url, fe.created_at
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        WHERE fe.market_day_id = ? AND fe.status = 'Active'
        ORDER BY fe.id
    """, [md_id]).fetchall()

    for r in fe_rows:
        urls = parse_photo_paths(r['photo_drive_url'])
        total_amount = r['amount']

        # Determine number of checks: use photo count, else check_count,
        # else derive from denomination, else 1
        num_checks = len(urls) if urls else (
            r['check_count'] if r['check_count'] and r['check_count'] > 0
            else (int(total_amount / denomination) if denomination > 0
                  else 1))
        if num_checks < 1:
            num_checks = 1

        check_amount = (round(total_amount / num_checks, 2)
                        if num_checks > 1 else total_amount)

        for i in range(num_checks):
            result.append({
                'Entry ID': f"FE-{r['id']}-{i + 1}",
                'Transaction ID': '',
                'Timestamp': r['created_at'] or '',
                'Vendor': r['vendor'],
                'Check Amount': check_amount,
                'Check': f"{i + 1} of {num_checks}",
                'Total Amount': total_amount,
                'Source': 'FMNP Entry',
                'Entered By': r['entered_by'] or '',
                'Notes': r['notes'] or '',
                'Photo': urls[i] if i < len(urls) else '',
            })

    # ── Source B: payment_line_items where method = FMNP ──
    pli_rows = conn.execute("""
        SELECT pl.id, pl.method_amount, pl.photo_drive_url, pl.created_at,
               t.fam_transaction_id, v.name AS vendor
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        JOIN vendors v ON t.vendor_id = v.id
        WHERE t.market_day_id = ?
          AND pl.method_name_snapshot = 'FMNP'
          AND t.status IN ('Confirmed', 'Adjusted')
        ORDER BY pl.id
    """, [md_id]).fetchall()

    for r in pli_rows:
        urls = parse_photo_paths(r['photo_drive_url'])
        total_amount = r['method_amount']
        txn_id = r['fam_transaction_id'] or ''

        num_checks = len(urls) if urls else (
            int(total_amount / denomination) if denomination > 0 else 1)
        if num_checks < 1:
            num_checks = 1

        check_amount = (round(total_amount / num_checks, 2)
                        if num_checks > 1 else total_amount)

        for i in range(num_checks):
            result.append({
                'Entry ID': f"PAY-{r['id']}-{i + 1}",
                'Transaction ID': txn_id,
                'Timestamp': r['created_at'] or '',
                'Vendor': r['vendor'],
                'Check Amount': check_amount,
                'Check': f"{i + 1} of {num_checks}",
                'Total Amount': total_amount,
                'Source': 'Payment',
                'Entered By': 'Payment',
                'Notes': '',
                'Photo': urls[i] if i < len(urls) else '',
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

    return [
        {'Timestamp': e['timestamp'],
         'Level': e['level'],
         'Area': e['module_label'],
         'Module': e['module'],
         'Message': e['message'],
         'Traceback': (e.get('traceback') or '').strip(),
         'App Version': __version__}
        for e in entries
    ]
