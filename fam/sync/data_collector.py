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
from fam.utils.money import cents_to_dollars

logger = logging.getLogger('fam.sync.data_collector')


def collect_sync_data(market_day_id: Optional[int] = None) -> dict[str, list[dict]]:
    """Collect data for all sync tabs.

    If *market_day_id* is None, collects data from ALL market days
    on this device so the full history appears in Google Sheets.
    Returns ``{sheet_name: rows}``.

    v2.0.1: When *market_day_id* is set (narrow-scope auto-sync),
    per-market-day collectors run only for that day BUT
    whole-dataset collectors (``Vendor Reimbursement``) still run
    against ALL market days.  Aggregate totals must reflect the
    full history regardless of which day triggered the sync;
    earlier behaviour silently overwrote multi-day vendor totals
    with single-day numbers on every auto-sync.
    """
    conn = get_connection()
    did = get_device_id() or ''

    # v2.0.1: open a single read transaction so every per-tab
    # collector below sees the SAME SQLite snapshot.  WAL gives
    # us snapshot isolation for free as long as one BEGIN is
    # held across all the SELECTs.  Without this, the main
    # thread can confirm a payment between collectors and
    # Vendor Reimbursement / Detailed Ledger end up reflecting
    # different points in time on the same sheet sync.
    try:
        conn.execute("BEGIN")
    except Exception:
        # Connection might already be in a transaction in
        # rare paths (e.g. nested test fixtures).  Continue —
        # at worst we lose the snapshot guarantee but still
        # produce valid (if slightly inconsistent) data.
        # v2.0.1: bumped from debug → warning so the degraded
        # snapshot state appears in the Error Log report.
        logger.warning(
            "collect_sync_data: could not BEGIN read transaction "
            "(continuing with degraded snapshot isolation)",
            exc_info=True)

    # All known market days — used by whole-dataset collectors.
    all_md_rows = conn.execute(
        "SELECT id FROM market_days ORDER BY date"
    ).fetchall()
    all_md_ids = [r['id'] for r in all_md_rows]

    # Per-md collector scope — narrow when caller specified, else full.
    if market_day_id is not None:
        md_ids = [market_day_id]
    else:
        md_ids = list(all_md_ids)
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
        """Add market_code and device_id to the end of every row.

        v1.9.10 follow-up (2026-05-01): a row that ALREADY carries a
        non-empty ``device_id`` (the originating device for an
        audit-log entry) is preserved as-is.  The earlier
        unconditional overwrite silently rewrote the originating
        device on cross-device-imported audit_log rows, breaking
        cross-device audit attribution.  Audit Log rows now keep
        the device that made the change (see _collect_activity_log).
        """
        out: list[dict] = []
        for r in rows:
            existing_did = r.get('device_id')
            row_did = existing_did if existing_did else did
            out.append({**r, 'market_code': mc, 'device_id': row_did})
        return out

    # Per-market-day collectors — run once per market day, combine rows
    per_md_collectors = [
        ('FAM Match Report',     lambda c, mid: _collect_fam_match(c, mid)),
        ('Detailed Ledger',      lambda c, mid: _collect_detailed_ledger(c, mid)),
        ('Transaction Log',      lambda c, mid: _collect_transaction_log(mid)),
        ('Activity Log',         lambda c, mid: _collect_activity_log(c, mid)),
        ('Geolocation',          lambda c, mid: _collect_geolocation(c, mid)),
        ('FMNP Entries',         lambda c, mid: _collect_fmnp_entries(c, mid)),
        ('Market Day Summary',   lambda c, mid: _collect_market_day_summary(c, mid)),
        ('Generated Rewards',    lambda c, mid: _collect_generated_rewards(c, mid)),
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

    # Whole-dataset collectors — run once across ALL market days
    # (NOT the narrow-scope ``md_ids``).  Vendor Reimbursement
    # aggregates lifetime totals per (market, vendor); narrowing
    # it to a single day would replace rows on the shared sheet
    # with single-day-only totals on every auto-sync.
    if is_sync_tab_enabled('Vendor Reimbursement') and all_md_ids:
        try:
            vr_rows = _collect_vendor_reimbursement(conn, all_md_ids)
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

    # End the snapshot read transaction.  COMMIT is correct here
    # (no writes) and releases the WAL snapshot so other writers
    # aren't blocked longer than needed.
    try:
        conn.commit()
    except Exception:
        # v2.0.1: bumped from debug → warning.  A failed commit
        # on a read-only transaction is rare but can indicate a
        # locked DB or a connection in a bad state — should reach
        # the Error Log report.
        logger.warning(
            "collect_sync_data: could not commit read transaction",
            exc_info=True)

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
    FAM Match, [one column per payment method], FMNP (External),
    Check Payable To, Address.

    Column semantics (v1.9.10+ per user request):

      * Per-method columns (SNAP, Cash, Food RX, JH Food Bucks,
        JH Tokens, FMNP, …) show ``SUM(pli.customer_charged)`` —
        the physical instruments the customer handed over.  This
        lets a market manager see "how many $2 Food Bucks does
        this vendor need to redeem?" at a glance.
      * The new ``FAM Match`` column shows ``SUM(pli.match_amount)``
        — FAM's contribution post-forfeit-reduction.  Aggregated
        across all methods so the manager sees total FAM
        responsibility per vendor in one number.
      * ``Total Due to Vendor`` = ``SUM(t.receipt_total)`` (vendor
        reimbursement contract — unchanged).  Math identity:
        ``Σ(method-cols) + FAM Match + FMNP (External) = Total
        Due to Vendor`` (within penny-rec tolerance).

    Pre-v1.9.10, the per-method columns showed
    ``SUM(pli.method_amount)`` (= customer + match) which conflated
    physical-instrument counts with FAM contribution and made the
    report ambiguous when forfeit/cap reduced match.
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

    # Per-method physical-instrument totals (customer_charged) and
    # per-vendor FAM Match aggregate (match_amount).  Two queries
    # let the manager see (a) how many tokens/checks the vendor
    # needs to redeem AND (b) how much FAM owes the vendor for
    # match — separately.
    method_rows = conn.execute(f"""
        SELECT v.name AS vendor,
               m.name AS market_name,
               pl.method_name_snapshot AS method,
               COALESCE(SUM(pl.customer_charged), 0) AS customer_total,
               COALESCE(SUM(pl.match_amount), 0) AS match_total,
               COALESCE(SUM(pl.method_amount), 0) AS method_total
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        {where}
        GROUP BY m.id, v.id, v.name, pl.method_name_snapshot
    """, params).fetchall()

    # v1.9.10 follow-up (2026-05-01): keep ALL money values in
    # integer cents inside the working dict and convert to floats
    # at the very end.  The earlier code accumulated
    # ``cents_to_dollars(...)`` floats with `+=`, which introduces
    # sub-cent drift (0.1 + 0.2 == 0.30000000000000004) that can
    # cause the row identity ``Σ method-cols + FAM Match +
    # FMNP_External == Total Due`` to fail by 1¢ at scale.  Cents
    # arithmetic is exact.
    # v1.9.10 follow-up (2026-05-01, onsite report): the per-method
    # column shows the **customer-paid** portion for ordinary payment
    # methods (= customer_charged), which is what the vendor will
    # redeem in physical scrip / EBT receipts.  ``Unallocated Funds``
    # is the exception: customer_charged is intentionally 0 (the
    # customer didn't hand it over — FAM absorbed the gap during a
    # customer-gone adjustment), but the vendor IS still owed that
    # amount and FAM will pay it directly.  Showing 0 in the UF
    # column made the row identity
    # ``Σ method-cols + FAM Match + FMNP_External == Total Due``
    # fail by exactly the absorbed amount.  Now: for the system-
    # managed Unallocated Funds row use ``method_amount`` (the
    # absorbed loss) so the row balances to receipt_total.
    from fam.models.payment_method import UNALLOCATED_FUNDS_NAME
    all_methods = sorted({r['method'] for r in method_rows})
    method_cents_by_vendor: dict[tuple, dict[str, int]] = {}
    fam_match_by_vendor: dict[tuple, int] = {}
    for r in method_rows:
        key = (r['market_name'], r['vendor'])
        if r['method'] == UNALLOCATED_FUNDS_NAME:
            value = r['method_total']
        else:
            value = r['customer_total']
        method_cents_by_vendor.setdefault(key, {})[r['method']] = value
        fam_match_by_vendor[key] = (
            fam_match_by_vendor.get(key, 0) + r['match_total'])

    # Internal "row in cents" representation; converted to dollars
    # at the end (see _emit_row_in_dollars below).
    vendor_dict_cents: dict[tuple, dict] = {}
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
            '_total_due_cents': r['gross_sales'],
            '_fam_match_cents': fam_match_by_vendor.get(key, 0),
            '_fmnp_external_cents': 0,
            '_method_cents': dict(method_cents_by_vendor.get(key, {})),
            'Check Payable To': r['check_payable_to'],
            'Address': _build_vendor_address(r),
        }
        vendor_dict_cents[key] = row

    # Merge external FMNP entries (cents-only)
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
        if key in vendor_dict_cents:
            vendor_dict_cents[key]['_fmnp_external_cents'] = r['fmnp_total']
            vendor_dict_cents[key]['_total_due_cents'] += r['fmnp_total']
            existing = set(vendor_dict_cents[key]['Date(s)'].split(',')) \
                if vendor_dict_cents[key]['Date(s)'] else set()
            new_dates = set((r['fmnp_dates'] or '').split(','))
            all_dates = (existing | new_dates) - {''}
            vendor_dict_cents[key]['Date(s)'] = ','.join(sorted(all_dates))
        else:
            month_str = ''
            if r['fmnp_dates']:
                month_str = datetime.strptime(r['fmnp_dates'][:7], '%Y-%m').strftime('%B')
            vendor_dict_cents[key] = {
                'Market Name': r['market_name'],
                'Vendor': r['vendor'],
                'Month': month_str,
                'Date(s)': r['fmnp_dates'] or '',
                '_total_due_cents': r['fmnp_total'],
                '_fam_match_cents': 0,
                '_fmnp_external_cents': r['fmnp_total'],
                '_method_cents': {},
                'Check Payable To': r['check_payable_to'],
                'Address': _build_vendor_address(r),
            }

    # Final pass — emit dollar values from the integer-cents
    # accumulators.  ``cents_to_dollars`` is called exactly once
    # per money field, so no float-accumulation drift.
    output: list[dict] = []
    for key in sorted(vendor_dict_cents.keys()):
        rc = vendor_dict_cents[key]
        out_row = {
            'Market Name': rc['Market Name'],
            'Vendor': rc['Vendor'],
            'Month': rc['Month'],
            'Date(s)': rc['Date(s)'],
            'Total Due to Vendor': cents_to_dollars(rc['_total_due_cents']),
            'FAM Match': cents_to_dollars(rc['_fam_match_cents']),
        }
        for m in all_methods:
            out_row[m] = cents_to_dollars(rc['_method_cents'].get(m, 0))
        out_row['FMNP (External)'] = cents_to_dollars(rc['_fmnp_external_cents'])
        out_row['Check Payable To'] = rc['Check Payable To']
        out_row['Address'] = rc['Address']
        output.append(out_row)
    return output


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

    # ``FAM Absorbed`` (schema v25+) — non-zero only for the
    # Unallocated Funds system method.  Mirrors the in-app FAM Match
    # Report so the synced spreadsheet has the same shape regardless
    # of whether any losses occurred during the period.
    from fam.models.payment_method import UNALLOCATED_FUNDS_NAME

    result = []
    for r in rows:
        is_absorbed = (r['method'] == UNALLOCATED_FUNDS_NAME)
        result.append({
            'Payment Method': r['method'],
            'Date': md_date,
            'Total Allocated': cents_to_dollars(r['total_allocated']),
            'Total FAM Match': cents_to_dollars(r['total_fam_match']),
            'FAM Absorbed': (cents_to_dollars(r['total_allocated'])
                             if is_absorbed else 0),
        })

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
            'Total Allocated': cents_to_dollars(fmnp_total),
            'Total FAM Match': 0,
            'FAM Absorbed': 0,
        })

    return result


def _collect_detailed_ledger(conn, md_id: int) -> list[dict]:
    """Detailed Ledger — mirrors reports_screen.py L660-743."""
    from fam.utils.photo_paths import parse_photo_paths

    rows = conn.execute("""
        SELECT t.id AS txn_id, t.fam_transaction_id, v.name AS vendor,
               t.receipt_total, t.status, t.created_at,
               COALESCE(co.customer_label, '') AS customer_id,
               COALESCE(co.zip_code, '') AS zip_code,
               COALESCE(SUM(pl.customer_charged), 0) AS customer_paid,
               COALESCE(SUM(pl.match_amount), 0) AS fam_match,
               GROUP_CONCAT(pl.method_name_snapshot || ': $' ||
                   PRINTF('%.2f', pl.method_amount / 100.0), ', ') AS methods
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        LEFT JOIN customer_orders co ON t.customer_order_id = co.id
        LEFT JOIN payment_line_items pl ON pl.transaction_id = t.id
        WHERE t.market_day_id = ? AND t.status != 'Draft'
        -- NOTE: Intentionally includes Voided transactions.  The sync
        -- Detailed Ledger serves as an audit trail (all statuses visible).
        -- The Reports UI filters to Confirmed+Adjusted only.  These are
        -- different by design — see TestVoidedTransactions in test_sync.py.
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
            # v2.0.6: zip_code from the customer_order so coordinators
            # can analyse which customer demographics use which payment
            # methods at which vendors.  Empty string when the order
            # has no zip captured (legacy data, FMNP-only entries
            # appended below, etc.) — never NULL.
            'Zip Code': r['zip_code'] or '',
            'Vendor': r['vendor'],
            'Receipt Total': cents_to_dollars(r['receipt_total']),
            'Customer Paid': cents_to_dollars(r['customer_paid']),
            'FAM Match': cents_to_dollars(r['fam_match']),
            'Status': r['status'],
            'Payment Methods': r['methods'] or '',
        }
        # Include payment photo URLs if any
        if r['txn_id'] in txn_photos:
            row_dict['Photos'] = ' | '.join(txn_photos[r['txn_id']])
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
            # External FMNP entries aren't tied to a customer_order so
            # zip is always empty — column included for schema parity.
            'Zip Code': '',
            'Vendor': r['vendor'],
            'Receipt Total': cents_to_dollars(r['amount']),
            'Customer Paid': 0,
            'FAM Match': cents_to_dollars(r['amount']),
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
            # v2.0.6: include customer label + zip code so audit-trail
            # entries can be correlated with customer demographics.
            # Empty when the audit row doesn't tie to a customer-bearing
            # transaction (e.g. system-level OPEN/CLOSE on market_days).
            'Customer': r.get('customer_label') or '',
            'Zip Code': r.get('zip_code') or '',
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
         'App Version': r['app_version'] or '',
         'device_id': r['device_id'] or ''}
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
         'Total Spend': cents_to_dollars(r['total_spend']),
         'Total FAM Match': cents_to_dollars(r['total_match'])}
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
        # v2.0.1: was silently swallowed.  An FMNP-method lookup
        # failure means the FMNP collector falls back to denom=0
        # which would mis-split multi-check entries — surface it.
        logger.warning(
            "collect_sync_data: could not resolve FMNP method "
            "denomination, falling back to 0 (multi-check splits "
            "may be incorrect)",
            exc_info=True)
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
        total_amount_cents = r['amount']

        # Determine number of checks: use photo count, else check_count,
        # else derive from denomination, else 1
        num_checks = len(urls) if urls else (
            r['check_count'] if r['check_count'] and r['check_count'] > 0
            else (int(total_amount_cents / denomination) if denomination > 0
                  else 1))
        if num_checks < 1:
            num_checks = 1

        base_check_cents = total_amount_cents // num_checks
        remainder_cents = total_amount_cents % num_checks

        for i in range(num_checks):
            check_cents = base_check_cents + (1 if i < remainder_cents else 0)
            result.append({
                'Entry ID': f"FE-{r['id']}-{i + 1}",
                'Transaction ID': '',
                'Timestamp': r['created_at'] or '',
                # v2.0.6: Source A entries from the dedicated FMNP
                # Entry tab are not tied to a customer order — empty
                # strings here for column-schema parity with Source B.
                'Customer': '',
                'Zip Code': '',
                'Vendor': r['vendor'],
                'Check Amount': cents_to_dollars(check_cents),
                'Check': f"{i + 1} of {num_checks}",
                'Total Amount': cents_to_dollars(total_amount_cents),
                'Source': 'FMNP Entry',
                'Entered By': r['entered_by'] or '',
                'Notes': r['notes'] or '',
                'Photo': urls[i] if i < len(urls) else '',
            })

    # ── Source B: payment_line_items where method = FMNP ──
    #
    # v2.0.1 fix: the "Check Amount" / "Total Amount" columns
    # represent the **physical face value** of the FMNP scrip the
    # customer handed over — i.e. ``customer_charged``.  Earlier
    # versions used ``method_amount`` here, which equals
    # ``customer_charged + match_amount`` and is double the face
    # value when match is 100% (the FMNP default).  Source A
    # (fmnp_entries.amount) is the face value.  Both sources must
    # agree so vendor redemption reports tally correctly.
    pli_rows = conn.execute("""
        SELECT pl.id, pl.customer_charged, pl.method_amount,
               pl.photo_drive_url, pl.created_at,
               t.fam_transaction_id, v.name AS vendor,
               co.customer_label, co.zip_code
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        JOIN vendors v ON t.vendor_id = v.id
        LEFT JOIN customer_orders co ON t.customer_order_id = co.id
        WHERE t.market_day_id = ?
          AND pl.method_name_snapshot = 'FMNP'
          AND t.status IN ('Confirmed', 'Adjusted')
        ORDER BY pl.id
    """, [md_id]).fetchall()

    for r in pli_rows:
        urls = parse_photo_paths(r['photo_drive_url'])
        # Physical face value (what the vendor will redeem at the bank).
        total_amount_cents = r['customer_charged']
        txn_id = r['fam_transaction_id'] or ''

        num_checks = len(urls) if urls else (
            int(total_amount_cents / denomination) if denomination > 0 else 1)
        if num_checks < 1:
            num_checks = 1

        base_check_cents = total_amount_cents // num_checks
        remainder_cents = total_amount_cents % num_checks

        for i in range(num_checks):
            check_cents = base_check_cents + (1 if i < remainder_cents else 0)
            result.append({
                'Entry ID': f"PAY-{r['id']}-{i + 1}",
                'Transaction ID': txn_id,
                'Timestamp': r['created_at'] or '',
                # v2.0.6: customer + zip code on payment-flow FMNP
                # entries so the FMNP Entries sheet can correlate
                # check redemptions with customer demographics.
                # Source A (manual FMNP Entry tab) is not tied to a
                # customer order — column included with empty string
                # for schema parity.
                'Customer': r['customer_label'] or '',
                'Zip Code': r['zip_code'] or '',
                'Vendor': r['vendor'],
                'Check Amount': cents_to_dollars(check_cents),
                'Check': f"{i + 1} of {num_checks}",
                'Total Amount': cents_to_dollars(total_amount_cents),
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
        'Total Receipts': cents_to_dollars(row['total_receipts']),
        'Total Customer Paid': cents_to_dollars(row['total_customer_paid']),
        'Total FAM Match': cents_to_dollars(row['total_fam_match']),
    }]


def _collect_error_log() -> list[dict]:
    """Error Log — parse fam_manager.log for errors and warnings.

    Syncs application errors to Google Sheets for remote troubleshooting.
    Includes full tracebacks, app version, and device identity.

    Per-entry App Version comes from the embedded ``[vX.Y.Z]`` token
    each log line carries (v1.9.9+).  Pre-upgrade entries that lack
    the token surface as ``Unknown`` rather than being mis-attributed
    to the *current* ``__version__`` — that re-attribution was the
    v1.9.9 onsite finding ("upgrading rewrote my error history").

    v2.0.5 fix: ``Message`` cell now embeds the full traceback when
    one is present.  Pre-fix the Message column held only the first
    line of the log entry (e.g. ``"Unhandled exception:"``) and the
    multi-line traceback lived in a separate ``Traceback`` column.
    Coordinators triaging a crash from the Sheet had to (a) know the
    Traceback column existed, (b) scroll horizontally to find it,
    and (c) realize the full traceback was there.  Combining them
    matches the local Reports → Error Log detail-panel format
    (Time / Level / Area / Module / Message / Traceback) so the
    Sheet and the local view show the SAME structured content.
    The ``Traceback`` column is preserved for backward-compat with
    Sheets that already have it (so existing dashboards / filters
    keep working).
    """
    from fam.utils.logging_config import get_log_path
    from fam.utils.log_reader import parse_log_file

    log_path = get_log_path()
    entries = parse_log_file(log_path, limit=500)

    rows = []
    for e in entries:
        first_line = e.get('message') or ''
        tb = (e.get('traceback') or '').strip()
        # Combine into a single multi-line Message cell.  Sheets
        # cells preserve newlines (they only render the first line
        # by default but click-to-expand shows all of it).
        if tb:
            full_message = f"{first_line}\n\nTraceback:\n{tb}"
        else:
            full_message = first_line
        rows.append({
            'Timestamp': e['timestamp'],
            'Level': e['level'],
            'Area': e['module_label'],
            'Module': e['module'],
            'Message': full_message,
            'Traceback': tb,
            'App Version': e.get('app_version') or 'Unknown',
        })
    return rows


def _collect_generated_rewards(conn, md_id: int) -> list[dict]:
    """Generated Rewards (v1.9.10+).

    Read-only view of the ``generated_rewards`` snapshot table.
    Rows are written once at payment-confirmation time and never
    modified after — so this collector returns the historical
    record, NOT a recomputation.  Specifically:

      * Pre-feature transactions never appear (no rows for them).
      * Disabling the rewards feature does NOT wipe rows from the
        report — the snapshot persists.
      * Rule edits / deletions do NOT retro-apply.
      * Voided / adjusted transactions do NOT modify reward rows
        (the cashier already handed the tokens; this is the
        receipt-of-record).

    Critically: this is NOT a financial report.  It does not
    affect vendor reimbursement, FAM match, or any per-line
    invariant.  See ``docs/FINANCIAL_FORMULA.md § 11``.
    """
    try:
        rows = conn.execute("""
            SELECT gr.*,
                   co.customer_label,
                   co.zip_code,
                   m.name AS market_name,
                   md.date AS market_date
            FROM generated_rewards gr
            JOIN customer_orders co
              ON gr.customer_order_id = co.id
            JOIN market_days md
              ON gr.market_day_id = md.id
            JOIN markets m
              ON md.market_id = m.id
            WHERE gr.market_day_id = ?
            ORDER BY gr.id
        """, (md_id,)).fetchall()
    except Exception:
        logger.exception(
            "Failed to read generated_rewards for market_day %s",
            md_id)
        return []

    return [
        {
            'Market Name': r['market_name'],
            'Date': r['market_date'],
            'Customer': r['customer_label'],
            # v2.0.6: zip code from customer_order so coordinator can
            # see which customer demographics earned which rewards.
            'Zip Code': r['zip_code'] or '',
            'Source Method': r['source_method_name_snapshot'],
            'Source Total': cents_to_dollars(r['source_total_cents']),
            'Threshold': cents_to_dollars(r['threshold_cents']),
            'Reward Method': r['reward_method_name_snapshot'],
            'Reward Unit': cents_to_dollars(r['reward_unit_cents']),
            'Units Earned': r['n_units'],
            'Reward Total': cents_to_dollars(r['reward_total_cents']),
            'Generated At': r['generated_at'] or '',
            'Generated By': r['generated_by'] or '',
        }
        for r in rows
    ]
