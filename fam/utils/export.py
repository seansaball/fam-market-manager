"""CSV and optional PDF export utilities."""

import csv
import logging
import os
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)


def export_dataframe_to_csv(df: pd.DataFrame, filepath: str) -> str:
    """Export a pandas DataFrame to CSV with market identity columns.

    Inserts ``market_code`` and ``device_id`` as the first two columns
    so the finance team can identify the source market/device.
    """
    from fam.utils.app_settings import get_market_code, get_device_id
    code = get_market_code() or ''
    device = get_device_id() or ''
    df = df.copy()
    df.insert(0, 'device_id', device)
    df.insert(0, 'market_code', code)
    df.to_csv(filepath, index=False)
    return filepath


def generate_export_filename(report_name: str, extension: str = "csv") -> str:
    """Generate a timestamped filename for an export.

    When a market code is configured, it is included in the filename
    (e.g. ``fam_DT_vendor_reimbursement_20260306_140530.csv``).
    """
    from fam.utils.app_settings import get_market_code
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = report_name.replace(" ", "_").lower()
    code = get_market_code()
    if code:
        return f"fam_{code}_{safe_name}_{timestamp}.{extension}"
    return f"fam_{safe_name}_{timestamp}.{extension}"


def export_vendor_reimbursement(data: list[dict], filepath: str) -> str:
    """Export vendor reimbursement report to CSV."""
    df = pd.DataFrame(data)
    return export_dataframe_to_csv(df, filepath)


def export_fam_match_report(data: list[dict], filepath: str) -> str:
    """Export FAM Match breakdown report to CSV."""
    df = pd.DataFrame(data)
    return export_dataframe_to_csv(df, filepath)


def export_detailed_ledger(data: list[dict], filepath: str) -> str:
    """Export detailed transaction ledger to CSV."""
    df = pd.DataFrame(data)
    return export_dataframe_to_csv(df, filepath)


def export_activity_log(data: list[dict], filepath: str) -> str:
    """Export full activity / audit log to CSV."""
    df = pd.DataFrame(data)
    return export_dataframe_to_csv(df, filepath)


def export_geolocation_report(data: list[dict], filepath: str) -> str:
    """Export geolocation zip code report to CSV."""
    df = pd.DataFrame(data)
    return export_dataframe_to_csv(df, filepath)


def export_transaction_log(data: list[dict], filepath: str) -> str:
    """Export transaction log report to CSV."""
    df = pd.DataFrame(data)
    return export_dataframe_to_csv(df, filepath)


def export_error_log(data: list[dict], filepath: str) -> str:
    """Export error log entries to CSV."""
    df = pd.DataFrame(data)
    return export_dataframe_to_csv(df, filepath)


# ── Automatic ledger backup ──────────────────────────────────────

def write_ledger_backup():
    """Write a human-readable text backup of the **entire** database ledger.

    Produces ``fam_ledger_backup.txt`` next to the database file.  Called
    automatically after payment confirmations, adjustments, voids, and
    market-day close so that volunteers always have a readable copy of
    every transaction even if the application will not open.

    The file is a complete mirror of all confirmed/adjusted/voided
    transactions across every market day — grouped by market, then by
    date — with per-day subtotals and grand totals.

    This function **never raises** — all errors are logged silently so it
    cannot interfere with the normal workflow.
    """
    try:
        _write_ledger_backup_inner()
    except Exception:
        logger.exception("Failed to write ledger backup")


# ── Column-formatting helpers ─────────────────────────────────────

_COL_HEADER = (
    f"  {'#':<4} {'Transaction ID':<22} {'Customer':<12} "
    f"{'Vendor':<24} {'Receipt':>10} {'Cust Paid':>10} "
    f"{'FAM Match':>10}  {'Status':<12} Payment Methods"
)
_COL_RULE = (
    f"  {'─' * 3:<4} {'─' * 20:<22} {'─' * 10:<12} "
    f"{'─' * 22:<24} {'─' * 9:>10} {'─' * 9:>10} "
    f"{'─' * 9:>10}  {'─' * 10:<12} {'─' * 15}"
)


def _fmt_vendor(name: str, max_len: int = 22) -> str:
    """Truncate long vendor names for column alignment."""
    return (name[:max_len - 2] + '..') if len(name) > max_len else name


def _write_ledger_backup_inner():
    """Core logic for write_ledger_backup (separated for clarity)."""
    import tempfile
    from fam.database.connection import get_connection, get_db_path

    conn = get_connection()

    # ── Gather ALL market days, grouped by market then date ────────
    market_days = conn.execute("""
        SELECT md.id, m.name AS market_name, md.date, md.status
        FROM market_days md
        JOIN markets m ON md.market_id = m.id
        ORDER BY m.name, md.date, md.id
    """).fetchall()

    if not market_days:
        return  # nothing to back up yet

    # Pre-query ALL transactions and FMNP entries, keyed by market_day_id
    all_txns = conn.execute("""
        SELECT t.market_day_id,
               t.fam_transaction_id, v.name AS vendor,
               t.receipt_total, t.status,
               COALESCE(co.customer_label, '') AS customer_id,
               COALESCE(SUM(pl.customer_charged), 0) AS customer_paid,
               COALESCE(SUM(pl.match_amount), 0) AS fam_match,
               GROUP_CONCAT(
                   pl.method_name_snapshot || ': $' ||
                   PRINTF('%.2f', pl.method_amount), ', '
               ) AS methods
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        LEFT JOIN customer_orders co ON t.customer_order_id = co.id
        LEFT JOIN payment_line_items pl ON pl.transaction_id = t.id
        WHERE t.status != 'Draft'
        GROUP BY t.id
        ORDER BY t.fam_transaction_id
    """).fetchall()

    all_fmnp = conn.execute("""
        SELECT fe.market_day_id, fe.id, v.name AS vendor,
               fe.amount, fe.check_count, fe.notes
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        WHERE fe.status = 'Active'
        ORDER BY fe.id
    """).fetchall()

    # Index by market_day_id for fast lookup
    txn_by_md: dict[int, list] = {}
    for r in all_txns:
        txn_by_md.setdefault(r['market_day_id'], []).append(r)

    fmnp_by_md: dict[int, list] = {}
    for r in all_fmnp:
        fmnp_by_md.setdefault(r['market_day_id'], []).append(r)

    # Count drafts per market day for the "not shown" note
    draft_counts = {}
    for row in conn.execute(
        "SELECT market_day_id, COUNT(*) AS n "
        "FROM transactions WHERE status = 'Draft' "
        "GROUP BY market_day_id"
    ).fetchall():
        draft_counts[row['market_day_id']] = row['n']

    # ── Summary statistics for the header ─────────────────────────
    total_market_days = len(market_days)
    market_names = set(md['market_name'] for md in market_days)
    total_txns = len(all_txns)
    total_fmnp = len(all_fmnp)

    # ── Build the text ────────────────────────────────────────────
    W = 115  # line width
    lines: list[str] = []

    lines.append("=" * W)
    lines.append("  FAM MARKET MANAGER — LEDGER BACKUP".center(W))
    lines.append("=" * W)
    lines.append("")
    from fam.utils.app_settings import get_market_code, get_device_id
    _code = get_market_code() or 'Not Set'
    _device = get_device_id() or 'Unknown'
    lines.append(f"  Database:     {os.path.basename(get_db_path())}")
    lines.append(f"  Market Code:  {_code}")
    lines.append(f"  Device ID:    {_device}")
    lines.append(f"  Backup at:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"  Markets: {len(market_names)}    "
                 f"Market Days: {total_market_days}    "
                 f"Transactions: {total_txns}    "
                 f"FMNP Entries: {total_fmnp}")
    lines.append("")

    # ── Grand totals accumulators ─────────────────────────────────
    grand_receipt = 0.0
    grand_customer = 0.0
    grand_match = 0.0
    grand_count = 0
    grand_voided = 0
    grand_fmnp_total = 0.0

    # ── Iterate by market → date ──────────────────────────────────
    current_market = None

    for md in market_days:
        md_id = md['id']

        # Market name separator (only when the market changes)
        if md['market_name'] != current_market:
            current_market = md['market_name']
            if lines[-1] != "":
                lines.append("")

        # Market day header
        status_note = md['status']
        if md['status'] == 'Open':
            status_note = "OPEN (in progress)"
        lines.append("\u2500" * W)
        lines.append(f"  {md['market_name']}  \u2014  {md['date']}  \u2014  {status_note}")
        lines.append("\u2500" * W)

        txn_rows = txn_by_md.get(md_id, [])
        fmnp_rows = fmnp_by_md.get(md_id, [])
        draft_n = draft_counts.get(md_id, 0)

        if not txn_rows and not fmnp_rows:
            lines.append("  No transactions recorded.")
            if draft_n:
                lines.append(f"  ({draft_n} draft transaction(s) not shown)")
            lines.append("")
            continue

        # Column header
        lines.append(_COL_HEADER)
        lines.append(_COL_RULE)

        day_receipt = 0.0
        day_customer = 0.0
        day_match = 0.0
        day_count = 0
        day_voided = 0

        for i, r in enumerate(txn_rows, 1):
            receipt = float(r['receipt_total'])
            cust_paid = float(r['customer_paid'])
            fam_match = float(r['fam_match'])
            is_voided = (r['status'] == 'Voided')

            if is_voided:
                day_voided += 1
            else:
                day_receipt += receipt
                day_customer += cust_paid
                day_match += fam_match
            day_count += 1

            vendor = _fmt_vendor(r['vendor'])
            lines.append(
                f"  {i:<4} {r['fam_transaction_id']:<22} "
                f"{r['customer_id']:<12} {vendor:<24} "
                f"${receipt:>9.2f} ${cust_paid:>9.2f} "
                f"${fam_match:>9.2f}  {r['status']:<12} "
                f"{r['methods'] or ''}"
            )

        # FMNP entries within this market day
        if fmnp_rows:
            lines.append("")
            lines.append("  --- FMNP (External) Entries ---")
            for r in fmnp_rows:
                day_count += 1
                amt = float(r['amount'])
                day_receipt += amt
                day_match += amt
                check_info = (
                    f"FMNP \u2013 {r['check_count']} checks"
                    if r['check_count'] else "FMNP (External)"
                )
                vendor = _fmt_vendor(r['vendor'])
                lines.append(
                    f"  {day_count:<4} {'FMNP-' + str(r['id']):<22} "
                    f"{'':<12} {vendor:<24} "
                    f"${amt:>9.2f} ${'0.00':>9} "
                    f"${amt:>9.2f}  {'FMNP Entry':<12} "
                    f"{check_info}"
                )
                grand_fmnp_total += amt

        # Draft note
        if draft_n:
            lines.append(f"  ({draft_n} draft transaction(s) not shown)")

        # Per-day subtotals
        lines.append("")
        voided_note = f"  Voided: {day_voided}" if day_voided else ""
        lines.append(
            f"  Subtotals:  "
            f"Receipt: ${day_receipt:,.2f}  |  "
            f"Customer Paid: ${day_customer:,.2f}  |  "
            f"FAM Match: ${day_match:,.2f}  |  "
            f"Transactions: {day_count}{voided_note}"
        )
        lines.append("")

        # Accumulate into grand totals
        grand_receipt += day_receipt
        grand_customer += day_customer
        grand_match += day_match
        grand_count += day_count
        grand_voided += day_voided

    # ── Grand totals ──────────────────────────────────────────────
    lines.append("=" * W)
    lines.append("  GRAND TOTALS".center(W))
    lines.append("=" * W)
    lines.append("")
    lines.append(f"  Total Receipts:        ${grand_receipt:,.2f}")
    lines.append(f"  Total Customer Paid:   ${grand_customer:,.2f}")
    lines.append(f"  Total FAM Match:       ${grand_match:,.2f}")
    lines.append(f"  Total FMNP (External): ${grand_fmnp_total:,.2f}")
    lines.append(f"  Transaction Count:     {grand_count}")
    if grand_voided:
        lines.append(f"  Voided (excluded):     {grand_voided}")
    lines.append("")
    lines.append("=" * W)
    lines.append("  This file is automatically maintained by FAM Market Manager.")
    lines.append("  It mirrors every transaction in the database. Open it with")
    lines.append("  any text editor to review data if the application cannot start.")
    lines.append("=" * W)
    lines.append("")

    # ── Atomic write (temp file + rename) ─────────────────────────
    backup_dir = os.path.dirname(os.path.abspath(get_db_path()))
    backup_path = os.path.join(backup_dir, "fam_ledger_backup.txt")

    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=backup_dir, prefix='.ledger_', suffix='.tmp', text=True,
        )
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        os.replace(tmp_path, backup_path)
    except PermissionError:
        # File may be open in Notepad — write to timestamped fallback
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        fallback = os.path.join(backup_dir, f"fam_ledger_backup_{ts}.txt")
        with open(fallback, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        logger.warning("Ledger file locked; wrote fallback: %s", fallback)
        return

    logger.info("Ledger backup written: %s (%d transactions)", backup_path, grand_count)
