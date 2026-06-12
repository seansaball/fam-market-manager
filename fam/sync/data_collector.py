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

    v2.0.9: Vendor Reimbursement now emits one row per
    (market × vendor × year-month) instead of one all-time row.
    The whole-dataset scope is preserved (every month is re-emitted
    on every sync), so a closed-day mutation in a prior month
    correctly updates THAT month's row without leaking into the
    current month's totals.
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
        # DEPRECATED tab, kept registered ON PURPOSE (R1,
        # 2026-06-11): the collector returns [] so the empty upsert
        # drains this device's rows from the shared sheet; the
        # coordinator deletes the tab once every market is on
        # v2.1.0+.  See _collect_fmnp_entries docstring.
        ('FMNP Entries',         lambda c, mid: _collect_fmnp_entries(c, mid)),
        # v2.1.0 (ENH-002): ALL external entries — FMNP, Food RX,
        # Food Bucks, … — one self-explanatory row per entry.
        ('External Payment Entries',
         lambda c, mid: _collect_external_payment_entries(c, mid)),
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
    # aggregates per-month totals per (market, vendor, year-month)
    # — see ``_collect_vendor_reimbursement``.  Each sync re-emits
    # every month so a closed-day mutation in any prior month
    # correctly updates THAT month's row.  Narrowing this collector
    # to a single day would replace months' worth of rows on the
    # shared sheet with single-day-only totals on every auto-sync.
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


def _format_year_month_label(year_month: str) -> str:
    """Return ``"April 2026"`` for ``"2026-04"`` (empty → empty).

    v2.0.9: human-readable label for the ``Month`` column on the
    Vendor Reimbursement sheet.  The sortable ``"2026-04"`` form
    lives in a separate ``Year-Month`` column for upsert keying.
    """
    if not year_month or len(year_month) < 7:
        return ''
    try:
        return datetime.strptime(year_month[:7], '%Y-%m').strftime('%B %Y')
    except ValueError:
        return ''


def _fmnp_method_id(conn) -> int:
    """The FMNP payment method's id — the tab-routing key (v2.1.0).

    Entries with this method id feed the FROZEN ``FMNP Entries``
    tab and the ``FMNP (External)`` Vendor Reimbursement column,
    byte-identically to pre-v2.1.0; every other method's entries
    feed the new ``External Payment Entries`` tab and per-method
    ``<Method> (External)`` columns.  Routing is by ID, never by
    name; display uses ``method_name_snapshot``.  Returns -1 when
    no FMNP method exists (no row can match — the v38 migration
    re-inserts FMNP whenever entries exist, so this is a fresh-DB
    edge only).
    """
    row = conn.execute(
        "SELECT id FROM payment_methods WHERE name = 'FMNP'"
    ).fetchone()
    return row['id'] if row else -1


def _external_payout_fields(amount_cents, match_percent_snapshot,
                            vendor_cashes_original_snapshot):
    """(payout_cents, basis_str) derived from an entry's SNAPSHOTS.

    Single call site for EP1 discipline in this module: payout is
    never stored and never read from current settings.  NULL match
    snapshots (impossible post-backfill, defensive only) fall back
    to the FMNP-identity config so the result equals face value.
    """
    from fam.utils.external_payout import (
        compute_external_payout_cents, reimbursement_basis)
    match_pct = (match_percent_snapshot
                 if match_percent_snapshot is not None else 100.0)
    cashes = bool(vendor_cashes_original_snapshot
                  if vendor_cashes_original_snapshot is not None
                  else 1)
    return (compute_external_payout_cents(amount_cents, match_pct,
                                          cashes),
            reimbursement_basis(amount_cents, match_pct, cashes))


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
    """Vendor Reimbursement — one row per (market × vendor × year-month).

    Layout: Market Name, Vendor, Month, Year-Month, Date(s),
    Total Due to Vendor, FAM Match, [one column per payment method],
    FMNP (External), Customer Forfeit, Check Payable To, Address.

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
        reimbursement contract — unchanged).  Math identity (holds
        within every monthly row):
        ``Σ(method-cols) + FAM Match - Customer Forfeit + FMNP
        (External) = Total Due to Vendor``.

    Pre-v1.9.10, the per-method columns showed
    ``SUM(pli.method_amount)`` (= customer + match) which conflated
    physical-instrument counts with FAM contribution and made the
    report ambiguous when forfeit/cap reduced match.

    v2.0.9: rows are now keyed by year-month so a coordinator can
    reconcile month-over-month.  The Month column is the human-
    readable ``"April 2026"`` form and the Year-Month column is the
    sortable ``"2026-04"`` form used as part of the row-identity for
    upsert (see ``SyncManager.SHEET_KEYS``).  Existing all-time
    cumulative rows on a v2.0.8 sheet do NOT carry a Year-Month and
    will appear as orphans on first v2.0.9 sync — coordinators
    delete them once after the upgrade (see v2.0.9 release notes).
    """
    from fam.models.transaction import active_tx_status_clause
    placeholders = ','.join('?' for _ in md_ids)
    where = (
        f"WHERE t.market_day_id IN ({placeholders}) "
        f"AND {active_tx_status_clause('t')}")
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
               strftime('%Y-%m', md.date) AS year_month,
               COALESCE(SUM(t.receipt_total), 0) AS gross_sales,
               GROUP_CONCAT(DISTINCT md.date) AS transaction_dates
               {addr_cols}
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        {where}
        GROUP BY m.id, v.id, v.name, year_month
        ORDER BY m.name, v.name, year_month
    """, params).fetchall()

    # Per-method physical-instrument totals (customer_charged) and
    # per-vendor FAM Match aggregate (match_amount).  Two queries
    # let the manager see (a) how many tokens/checks the vendor
    # needs to redeem AND (b) how much FAM owes the vendor for
    # match — separately.
    method_rows = conn.execute(f"""
        SELECT v.name AS vendor,
               m.name AS market_name,
               strftime('%Y-%m', md.date) AS year_month,
               pl.method_name_snapshot AS method,
               COALESCE(SUM(pl.customer_charged), 0) AS customer_total,
               COALESCE(SUM(pl.match_amount), 0) AS match_total,
               COALESCE(SUM(pl.method_amount), 0) AS method_total,
               COALESCE(SUM(pl.customer_forfeit_cents), 0) AS forfeit_total
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        JOIN vendors v ON t.vendor_id = v.id
        JOIN market_days md ON t.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        {where}
        GROUP BY m.id, v.id, v.name, year_month, pl.method_name_snapshot
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
    # v2.0.9: every accumulator is keyed by
    # ``(market_name, vendor, year_month)`` so each calendar month
    # gets its own row.  The earlier ``(market_name, vendor)`` key
    # silently merged all months into one cumulative row, which
    # made month-over-month reconciliation impossible (and the
    # ``Month`` column was misleading — it was just whichever date
    # happened to sort alphabetically first across all history).
    from fam.models.payment_method import UNALLOCATED_FUNDS_NAME
    all_methods = sorted({r['method'] for r in method_rows})
    method_cents_by_vendor: dict[tuple, dict[str, int]] = {}
    fam_match_by_vendor: dict[tuple, int] = {}
    # v2.0.7 (schema v36): per-vendor sum of Phase B customer-side
    # forfeit.  Surfaced as a "Customer Forfeit" column so the
    # row identity reads cleanly across:
    #   Σ(method-cols) + FAM Match + FMNP (External) = Total Due to Vendor
    #   Σ(method-cols) + Customer Forfeit            = Customer's physical handout
    # The vendor still gets exactly Total Due to Vendor (= receipt
    # total).  The forfeit is the unaccounted portion of customer-
    # paid denomination that didn't reach the vendor (e.g. customer
    # hands $10 Food RX to a $6.52 receipt → vendor reimbursed
    # $6.52, $3.48 forfeited).  Phase A (FAM match reduction) is
    # NOT counted here — those are unused FAM funds, not customer-
    # side losses.
    customer_forfeit_by_vendor: dict[tuple, int] = {}
    for r in method_rows:
        key = (r['market_name'], r['vendor'], r['year_month'])
        if r['method'] == UNALLOCATED_FUNDS_NAME:
            value = r['method_total']
        else:
            # v2.0.7+ denomination-integrity: per-method column
            # shows the customer's actual physical denomination
            # payment (= customer_charged + forfeit).  For
            # denominated methods this preserves the token face
            # value (e.g. $10 for 1 × $10 Food RX → $1.45 receipt
            # shows Food RX = $10, NOT $1.45).  For non-denom
            # methods forfeit is always 0, so this equals
            # customer_charged exactly (no behavior change).
            # The Customer Forfeit column then subtracts the
            # over-tender so the reconciliation reads:
            #   Σ(method-cols) + FAM Match - Customer Forfeit
            #     = Total Due to Vendor
            value = (r['customer_total']
                     + (r['forfeit_total'] or 0))
        method_cents_by_vendor.setdefault(key, {})[r['method']] = value
        fam_match_by_vendor[key] = (
            fam_match_by_vendor.get(key, 0) + r['match_total'])
        customer_forfeit_by_vendor[key] = (
            customer_forfeit_by_vendor.get(key, 0)
            + (r['forfeit_total'] or 0))

    # Internal "row in cents" representation; converted to dollars
    # at the end (see _emit_row_in_dollars below).
    vendor_dict_cents: dict[tuple, dict] = {}
    for r in vendor_rows:
        ym = r['year_month'] or ''
        key = (r['market_name'], r['vendor'], ym)
        # v2.0.9: ``Month`` is the human-readable "April 2026" form
        # (was just "April" pre-v2.0.9 — ambiguous once data spanned
        # more than 12 months) and ``Year-Month`` is the sortable
        # "2026-04" form used in the upsert key.
        month_str = _format_year_month_label(ym)
        row = {
            'Market Name': r['market_name'],
            'Vendor': r['vendor'],
            'Month': month_str,
            'Year-Month': ym,
            'Date(s)': r['transaction_dates'] or '',
            '_total_due_cents': r['gross_sales'],
            '_fam_match_cents': fam_match_by_vendor.get(key, 0),
            '_customer_forfeit_cents': customer_forfeit_by_vendor.get(key, 0),
            '_fmnp_external_cents': 0,
            '_external_method_cents': {},
            '_method_cents': dict(method_cents_by_vendor.get(key, {})),
            'Check Payable To': r['check_payable_to'],
            'Address': _build_vendor_address(r),
        }
        vendor_dict_cents[key] = row

    # Merge external FMNP entries (cents-only).
    # v2.1.0 (ENH-002): the FMNP-method filter keeps this FROZEN
    # column fed by FMNP entries only — non-FMNP external entries
    # merge separately below into their own <Method> (External)
    # columns.  Output-identical for all pre-v2.1.0 data.
    fmnp_method_id = _fmnp_method_id(conn)
    fmnp_where = (f"WHERE fe.market_day_id IN ({placeholders}) "
                  f"AND fe.status = 'Active' "
                  f"AND fe.payment_method_id = ?")
    fmnp_rows = conn.execute(f"""
        SELECT v.name AS vendor,
               m.name AS market_name,
               strftime('%Y-%m', md.date) AS year_month,
               {cpt_expr} AS check_payable_to,
               COALESCE(SUM(fe.amount), 0) AS fmnp_total,
               GROUP_CONCAT(DISTINCT md.date) AS fmnp_dates
               {addr_cols}
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        JOIN market_days md ON fe.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        {fmnp_where}
        GROUP BY m.id, v.id, v.name, year_month
    """, params + [fmnp_method_id]).fetchall()

    for r in fmnp_rows:
        ym = r['year_month'] or ''
        key = (r['market_name'], r['vendor'], ym)
        if key in vendor_dict_cents:
            # v2.0.9: same (market, vendor, year-month) — merge into
            # the existing monthly row.  Vendors with both
            # transactions and external FMNP entries within the same
            # month produce ONE row, not two.
            vendor_dict_cents[key]['_fmnp_external_cents'] = r['fmnp_total']
            vendor_dict_cents[key]['_total_due_cents'] += r['fmnp_total']
            existing = set(vendor_dict_cents[key]['Date(s)'].split(',')) \
                if vendor_dict_cents[key]['Date(s)'] else set()
            new_dates = set((r['fmnp_dates'] or '').split(','))
            all_dates = (existing | new_dates) - {''}
            vendor_dict_cents[key]['Date(s)'] = ','.join(sorted(all_dates))
        else:
            # FMNP-only vendor for this month — no matching
            # transaction-row to merge into.  The query already
            # groups by year_month so an FMNP-only vendor with
            # entries in multiple months produces one fmnp_row per
            # month, each landing here as its own monthly bucket.
            vendor_dict_cents[key] = {
                'Market Name': r['market_name'],
                'Vendor': r['vendor'],
                'Month': _format_year_month_label(ym),
                'Year-Month': ym,
                'Date(s)': r['fmnp_dates'] or '',
                '_total_due_cents': r['fmnp_total'],
                '_fam_match_cents': 0,
                '_customer_forfeit_cents': 0,
                '_fmnp_external_cents': r['fmnp_total'],
                '_external_method_cents': {},
                '_method_cents': {},
                'Check Payable To': r['check_payable_to'],
                'Address': _build_vendor_address(r),
            }

    # ── Merge non-FMNP external payment entries (v2.1.0/ENH-002) ──
    # Per-ENTRY payout derivation (rounding is per entry — EP1;
    # summing faces and applying match % to the sum could differ by
    # a cent), accumulated per (market, vendor, year-month, method).
    # Each method becomes a ``<Method> (External)`` column carrying
    # the FAM-OWED amount (what the reimbursement check needs), and
    # payouts add into Total Due to Vendor.  Row identity v2 (EP3):
    #   Σ(method-cols) + FAM Match - Customer Forfeit
    #     + FMNP (External) + Σ <Method> (External)
    #     = Total Due to Vendor
    # Old devices write blanks into these columns on their own rows
    # only (gsheets row.get(header, '') semantics) — which is also
    # semantically correct, since they cannot produce such entries.
    ext_entry_rows = conn.execute(f"""
        SELECT v.name AS vendor,
               m.name AS market_name,
               strftime('%Y-%m', md.date) AS year_month,
               md.date AS md_date,
               {cpt_expr} AS check_payable_to,
               fe.amount, fe.method_name_snapshot,
               fe.match_percent_snapshot,
               fe.vendor_cashes_original_snapshot
               {addr_cols}
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        JOIN market_days md ON fe.market_day_id = md.id
        JOIN markets m ON md.market_id = m.id
        WHERE fe.market_day_id IN ({placeholders})
          AND fe.status = 'Active'
          AND fe.payment_method_id != ?
    """, params + [fmnp_method_id]).fetchall()

    all_external_cols: set[str] = set()
    for r in ext_entry_rows:
        ym = r['year_month'] or ''
        key = (r['market_name'], r['vendor'], ym)
        payout_cents, _basis = _external_payout_fields(
            r['amount'], r['match_percent_snapshot'],
            r['vendor_cashes_original_snapshot'])
        col = f"{r['method_name_snapshot'] or 'Unknown'} (External)"
        all_external_cols.add(col)
        if key not in vendor_dict_cents:
            vendor_dict_cents[key] = {
                'Market Name': r['market_name'],
                'Vendor': r['vendor'],
                'Month': _format_year_month_label(ym),
                'Year-Month': ym,
                'Date(s)': '',
                '_total_due_cents': 0,
                '_fam_match_cents': 0,
                '_customer_forfeit_cents': 0,
                '_fmnp_external_cents': 0,
                '_external_method_cents': {},
                '_method_cents': {},
                'Check Payable To': r['check_payable_to'],
                'Address': _build_vendor_address(r),
            }
        row = vendor_dict_cents[key]
        ext = row['_external_method_cents']
        ext[col] = ext.get(col, 0) + payout_cents
        row['_total_due_cents'] += payout_cents
        existing = (set(row['Date(s)'].split(','))
                    if row['Date(s)'] else set())
        all_dates = (existing | {r['md_date'] or ''}) - {''}
        row['Date(s)'] = ','.join(sorted(all_dates))

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
            'Year-Month': rc['Year-Month'],
            'Date(s)': rc['Date(s)'],
            'Total Due to Vendor': cents_to_dollars(rc['_total_due_cents']),
            'FAM Match': cents_to_dollars(rc['_fam_match_cents']),
        }
        for m in all_methods:
            out_row[m] = cents_to_dollars(rc['_method_cents'].get(m, 0))
        out_row['FMNP (External)'] = cents_to_dollars(rc['_fmnp_external_cents'])
        # v2.1.0 (ENH-002): per-method external columns, FAM-owed
        # amounts, emitted for every row (0.0 when none) so the
        # column set is consistent across the batch.  Placed after
        # FMNP (External), before Customer Forfeit.  NOTE: on a
        # pre-existing spreadsheet the auto-widening appends new
        # headers at the END of the header row instead (writes are
        # by header name, so manual column reordering is safe).
        for ext_col in sorted(all_external_cols):
            out_row[ext_col] = cents_to_dollars(
                rc['_external_method_cents'].get(ext_col, 0))
        # v2.0.7: Customer Forfeit appears AFTER the per-method
        # columns and FMNP (External) so the row reads naturally
        # as (vendor reimbursement breakdown) → (customer-side
        # forfeit) → (admin metadata).  The math identity:
        #   Σ(method-cols) + FAM Match + FMNP (External)
        #     = Total Due to Vendor (vendor cashes this)
        #   Σ(method-cols) + Customer Forfeit
        #     = customer's physical handout (denominated face values)
        out_row['Customer Forfeit'] = cents_to_dollars(
            rc.get('_customer_forfeit_cents', 0))
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

    from fam.models.transaction import active_tx_status_clause
    rows = conn.execute(f"""
        SELECT pl.method_name_snapshot AS method,
               SUM(pl.method_amount) AS total_allocated,
               SUM(pl.match_amount) AS total_fam_match
        FROM payment_line_items pl
        JOIN transactions t ON pl.transaction_id = t.id
        WHERE t.market_day_id = ? AND {active_tx_status_clause('t')}
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

    # Add external FMNP total.  v2.1.0: FMNP-method filter keeps
    # this FROZEN row's feed identical; non-FMNP external methods
    # get their own rows below.
    fmnp_total = conn.execute("""
        SELECT COALESCE(SUM(fe.amount), 0) AS total
        FROM fmnp_entries fe
        WHERE fe.market_day_id = ? AND fe.status = 'Active'
          AND fe.payment_method_id = ?
    """, [md_id, _fmnp_method_id(conn)]).fetchone()['total']

    if fmnp_total > 0:
        result.append({
            'Payment Method': 'FMNP (External)',
            'Date': md_date,
            'Total Allocated': cents_to_dollars(fmnp_total),
            'Total FAM Match': 0,
            'FAM Absorbed': 0,
        })

    # v2.1.0 (ENH-002): one ``<Method> (External)`` row per non-FMNP
    # external method.  Total Allocated = Σ derived payouts (what
    # FAM pays out); Total FAM Match = Σ match components.  Additive
    # rows on this tab — keyed by Payment Method + Date, so they
    # never collide with existing rows and old devices are
    # unaffected.
    from fam.utils.external_payout import match_component_cents
    ext_rows = conn.execute("""
        SELECT fe.amount, fe.method_name_snapshot,
               fe.match_percent_snapshot,
               fe.vendor_cashes_original_snapshot
        FROM fmnp_entries fe
        WHERE fe.market_day_id = ? AND fe.status = 'Active'
          AND fe.payment_method_id != ?
    """, [md_id, _fmnp_method_id(conn)]).fetchall()

    ext_totals: dict[str, dict[str, int]] = {}
    for r in ext_rows:
        payout_cents, _ = _external_payout_fields(
            r['amount'], r['match_percent_snapshot'],
            r['vendor_cashes_original_snapshot'])
        match_pct = (r['match_percent_snapshot']
                     if r['match_percent_snapshot'] is not None
                     else 100.0)
        match_cents = match_component_cents(r['amount'], match_pct)
        name = r['method_name_snapshot'] or 'Unknown'
        bucket = ext_totals.setdefault(
            name, {'payout': 0, 'match': 0})
        bucket['payout'] += payout_cents
        bucket['match'] += match_cents

    for name in sorted(ext_totals):
        result.append({
            'Payment Method': f"{name} (External)",
            'Date': md_date,
            'Total Allocated': cents_to_dollars(
                ext_totals[name]['payout']),
            'Total FAM Match': cents_to_dollars(
                ext_totals[name]['match']),
            'FAM Absorbed': 0,
        })

    return result


def _collect_detailed_ledger(conn, md_id: int) -> list[dict]:
    """Detailed Ledger — mirrors reports_screen.py L660-743."""
    from fam.utils.photo_paths import parse_photo_paths

    # v2.0.7+ denomination-integrity:
    #   * customer_paid = customer_charged + forfeit (what the
    #     customer literally handed over, denomination-true).
    #   * Payment Methods column shows the same value broken down
    #     per method — NOT method_amount, which intermingles FAM
    #     match (e.g. 2 × $10 Food RX + $5.63 match would show
    #     "Food RX: $25.63", obscuring the true $20 token value).
    # The Customer Forfeit column subtracts the over-tender so:
    #   Customer Paid + FAM Match - Customer Forfeit = Receipt Total
    #
    # EXCEPTION — Unallocated Funds (schema v25+, system-managed):
    # this method records the FAM-absorbed gap from a customer-gone
    # adjustment.  customer_charged is intentionally 0 (the customer
    # didn't hand it over — FAM absorbed it), but the vendor IS
    # still owed that amount.  Without the CASE carve-out below the
    # Payment Methods column would render "Unallocated Funds: $0.00"
    # for every adjusted transaction, hiding the absorbed loss
    # (user-reported 2026-05-07).  Vendor Reimbursement already had
    # the same carve-out at the per-method aggregation; this mirrors
    # it in the per-row GROUP_CONCAT.
    from fam.models.payment_method import UNALLOCATED_FUNDS_NAME
    rows = conn.execute(f"""
        SELECT t.id AS txn_id, t.fam_transaction_id, v.name AS vendor,
               t.receipt_total, t.status, t.created_at,
               COALESCE(co.customer_label, '') AS customer_id,
               COALESCE(co.zip_code, '') AS zip_code,
               COALESCE(SUM(pl.customer_charged
                            + pl.customer_forfeit_cents), 0)
                   AS customer_paid,
               COALESCE(SUM(pl.match_amount), 0) AS fam_match,
               COALESCE(SUM(pl.customer_forfeit_cents), 0) AS customer_forfeit,
               GROUP_CONCAT(pl.method_name_snapshot || ': $' ||
                   PRINTF('%.2f',
                          CASE WHEN pl.method_name_snapshot
                                    = '{UNALLOCATED_FUNDS_NAME}'
                               THEN pl.method_amount / 100.0
                               ELSE (pl.customer_charged
                                     + pl.customer_forfeit_cents)
                                    / 100.0
                          END),
                   ', ') AS methods
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
            # v2.0.7 (schema v36): per-transaction customer-side
            # denomination forfeit.  Non-zero ONLY when the customer
            # handed a denomination unit larger than the receipt
            # remaining (e.g. $10 Food RX → $6.52 receipt = $3.48
            # forfeit).  Lets coordinators reconcile the customer's
            # physical handouts (denomination face values) against
            # what reached the vendor.
            'Customer Forfeit': cents_to_dollars(r['customer_forfeit']),
            'Status': r['status'],
            'Payment Methods': r['methods'] or '',
        }
        # Include payment photo URLs if any
        if r['txn_id'] in txn_photos:
            row_dict['Photos'] = ' | '.join(txn_photos[r['txn_id']])
        result.append(row_dict)

    # Append external FMNP entries.  v2.1.0: FMNP-method filter
    # keeps these FROZEN rows (FMNP- ids, FAM Match = face
    # convention) fed identically; non-FMNP methods get EXT- rows
    # below.
    fmnp_method_id = _fmnp_method_id(conn)
    fmnp_rows = conn.execute("""
        SELECT fe.id, v.name AS vendor, fe.amount, fe.check_count,
               fe.created_at
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        WHERE fe.market_day_id = ? AND fe.status = 'Active'
          AND fe.payment_method_id = ?
        ORDER BY fe.id
    """, [md_id, fmnp_method_id]).fetchall()

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
            # External FMNP entries never have customer-side forfeit
            # (the vendor matched the check directly at the booth;
            # FAM reimburses face value).
            'Customer Forfeit': 0,
            'Status': 'FMNP Entry',
            'Payment Methods': check_info,
        })

    # v2.1.0 (ENH-002): append non-FMNP external entries as EXT-
    # rows.  Conventions (mirroring the FMNP rows above, with the
    # generalized money split): Receipt Total = derived payout
    # (what FAM owes the vendor), FAM Match = match component,
    # Customer Paid = 0 (no booth customer), Forfeit = 0.
    from fam.utils.external_payout import match_component_cents
    ext_ledger_rows = conn.execute("""
        SELECT fe.id, v.name AS vendor, fe.amount, fe.check_count,
               fe.created_at, fe.method_name_snapshot,
               fe.match_percent_snapshot,
               fe.vendor_cashes_original_snapshot
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        WHERE fe.market_day_id = ? AND fe.status = 'Active'
          AND fe.payment_method_id != ?
        ORDER BY fe.id
    """, [md_id, fmnp_method_id]).fetchall()

    for r in ext_ledger_rows:
        payout_cents, _basis = _external_payout_fields(
            r['amount'], r['match_percent_snapshot'],
            r['vendor_cashes_original_snapshot'])
        match_pct = (r['match_percent_snapshot']
                     if r['match_percent_snapshot'] is not None
                     else 100.0)
        match_cents = match_component_cents(r['amount'], match_pct)
        name = r['method_name_snapshot'] or 'Unknown'
        info = (f"{name} (External) - {r['check_count']} items"
                if r['check_count'] else f"{name} (External)")
        result.append({
            'Transaction ID': f"EXT-{r['id']}",
            'Timestamp': r['created_at'] or '',
            'Customer': '',
            'Zip Code': '',
            'Vendor': r['vendor'],
            'Receipt Total': cents_to_dollars(payout_cents),
            'Customer Paid': 0,
            'FAM Match': cents_to_dollars(match_cents),
            'Customer Forfeit': 0,
            'Status': 'External Entry',
            'Payment Methods': info,
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
    """FMNP Entries — DEPRECATED tab, deliberately empty (design
    revision R1, 2026-06-11).

    FMNP entries now flow to the ``External Payment Entries`` tab
    alongside every other external method (one row per entry).  This
    collector returns ``[]`` ON PURPOSE: the tab stays registered in
    ``SHEET_KEYS`` / ``REQUIRED_SYNC_TABS`` so the empty upsert
    DRAINS this device's historical rows from the shared sheet on
    the next full sync (gsheets ``upsert_rows`` removes all of the
    device's rows when given no data; it tolerates an
    already-deleted worksheet and absent rows — see
    fam/sync/gsheets.py).  Old app versions keep writing the tab
    until they upgrade; once every market is on v2.1.0+ the tab is
    empty and the coordinator deletes it manually.

    Retired with the old tab (Sean, 2026-06-11): per-check row
    splitting (the new tab is one row per entry; all photo URLs ride
    in its Photos cell) and Source B booth-paid FMNP ``PAY-`` rows
    (booth FMNP remains fully visible in the Detailed Ledger as part
    of its transaction).

    Do NOT remove this function or the tab registration until the
    fleet is uniform and the tab has been deleted — removing them
    early would stop the drain and strand stale rows.
    """
    return []


def _collect_external_payment_entries(conn, md_id: int) -> list[dict]:
    """External Payment Entries — one row per entry, ALL methods
    (v2.1.0; FMNP included since design revision R1, 2026-06-11 —
    the old ``FMNP Entries`` tab is deprecated and drains).

    The per-entry AUDIT layer for the finance team: every row is
    SELF-EXPLANATORY (user requirement 2026-06-10).  Because entries
    snapshot their config at entry time, a settings correction
    produces old-wrong and new-right rows side by side; each row
    carries its own Match % / Vendor Cashes Original snapshots, the
    derived FAM Owes Vendor amount, and a plain-English
    Reimbursement Basis so any row can be audited in isolation —
    without knowing what Settings said on any given day.  FMNP rows
    read "Match only (… × 100%)" — FAM owes the match; the vendor
    cashes the check with the program.

    One ROW per entry (not per instrument).  The ``Instruments``
    column carries the count; all photo URLs ride in ``Photos``.
    Historical replication on upgrade is automatic: every sync
    re-emits the device's full history, so a freshly-upgraded
    market's complete FMNP record appears here on first sync.

    Voided (soft-deleted) entries ARE included with Status='Voided'
    — audit-trail precedent of the sync Detailed Ledger — and are
    excluded from every rollup (Vendor Reimbursement, FAM Match,
    ledger backup), which all filter status='Active'.
    """
    from fam.utils.photo_paths import parse_photo_paths

    rows = conn.execute("""
        SELECT fe.id, v.name AS vendor, fe.amount, fe.check_count,
               fe.notes, fe.entered_by, fe.photo_drive_url,
               fe.created_at, fe.status, md.date AS md_date,
               fe.method_name_snapshot, fe.match_percent_snapshot,
               fe.vendor_cashes_original_snapshot,
               fe.denomination_snapshot
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        JOIN market_days md ON fe.market_day_id = md.id
        WHERE fe.market_day_id = ?
        ORDER BY fe.id
    """, [md_id]).fetchall()

    result = []
    for r in rows:
        payout_cents, basis = _external_payout_fields(
            r['amount'], r['match_percent_snapshot'],
            r['vendor_cashes_original_snapshot'])
        # Instrument count: explicit count, else derived from the
        # entry's own denomination snapshot, else blank.
        if r['check_count'] and r['check_count'] > 0:
            instruments = r['check_count']
        elif r['denomination_snapshot']:
            instruments = r['amount'] // r['denomination_snapshot']
        else:
            instruments = ''
        urls = parse_photo_paths(r['photo_drive_url'])
        match_pct = r['match_percent_snapshot']
        result.append({
            'Entry ID': f"FE-{r['id']}",
            'Timestamp': r['created_at'] or '',
            'Market Day Date': r['md_date'] or '',
            'Vendor': r['vendor'],
            'Payment Method': r['method_name_snapshot'] or '',
            'Face Value': cents_to_dollars(r['amount']),
            'Instruments': instruments,
            'Match %': match_pct if match_pct is not None else '',
            'Vendor Cashes Original':
                'Yes' if r['vendor_cashes_original_snapshot'] else 'No',
            'FAM Owes Vendor': cents_to_dollars(payout_cents),
            'Reimbursement Basis': basis,
            'Status': ('Voided' if r['status'] == 'Deleted'
                       else (r['status'] or 'Active')),
            'Entered By': r['entered_by'] or '',
            'Notes': r['notes'] or '',
            'Photos': ' | '.join(urls) if urls else '',
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
