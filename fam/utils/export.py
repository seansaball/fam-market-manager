"""CSV and optional PDF export utilities."""

import csv
import logging
import os
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)


def export_dataframe_to_csv(df: pd.DataFrame, filepath: str) -> str:
    """Export a pandas DataFrame to CSV. Returns the full filepath."""
    df.to_csv(filepath, index=False)
    return filepath


def generate_export_filename(report_name: str, extension: str = "csv") -> str:
    """Generate a timestamped filename for an export."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = report_name.replace(" ", "_").lower()
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
    """Write a human-readable text backup of the current market day's ledger.

    Produces ``fam_ledger_backup.txt`` next to the database file.  Called
    automatically after payment confirmations, adjustments, voids, and
    market-day close so that volunteers always have a readable copy of
    transactions even if the application will not open.

    This function **never raises** — all errors are logged silently so it
    cannot interfere with the normal workflow.
    """
    try:
        _write_ledger_backup_inner()
    except Exception:
        logger.exception("Failed to write ledger backup")


def _write_ledger_backup_inner():
    """Core logic for write_ledger_backup (separated for clarity)."""
    from fam.database.connection import get_connection, get_db_path

    conn = get_connection()

    # Find the most recent market day (prefer open, fall back to latest)
    md = conn.execute("""
        SELECT md.id, m.name AS market_name, md.date, md.status
        FROM market_days md
        JOIN markets m ON md.market_id = m.id
        ORDER BY
            CASE md.status WHEN 'Open' THEN 0 ELSE 1 END,
            md.date DESC, md.id DESC
        LIMIT 1
    """).fetchone()

    if not md:
        return  # nothing to back up yet

    md_id = md['id']

    # ── Query transactions ────────────────────────────────────────
    txn_rows = conn.execute("""
        SELECT t.fam_transaction_id, v.name AS vendor,
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
        WHERE t.market_day_id = ?
        GROUP BY t.id
        ORDER BY t.fam_transaction_id
    """, (md_id,)).fetchall()

    # ── Query FMNP entries ────────────────────────────────────────
    fmnp_rows = conn.execute("""
        SELECT fe.id, v.name AS vendor, fe.amount,
               fe.check_count, fe.notes
        FROM fmnp_entries fe
        JOIN vendors v ON fe.vendor_id = v.id
        WHERE fe.market_day_id = ?
        ORDER BY fe.id
    """, (md_id,)).fetchall()

    # ── Build the text ────────────────────────────────────────────
    W = 105  # line width
    lines: list[str] = []

    lines.append("=" * W)
    lines.append("  FAM MARKET MANAGER — LEDGER BACKUP".center(W))
    lines.append("=" * W)
    lines.append("")
    lines.append(f"  Market:      {md['market_name']}")
    lines.append(f"  Date:        {md['date']}")
    lines.append(f"  Status:      {md['status']}")
    lines.append(f"  Backup at:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("-" * W)
    lines.append("  TRANSACTION LEDGER")
    lines.append("-" * W)
    lines.append("")

    # Column header
    lines.append(
        f"  {'#':<4} {'Transaction ID':<22} {'Customer':<12} "
        f"{'Vendor':<20} {'Receipt':>10} {'Cust Paid':>10} "
        f"{'FAM Match':>10}  {'Status':<12} Payment Methods"
    )
    lines.append(
        f"  {'─' * 3:<4} {'─' * 20:<22} {'─' * 10:<12} "
        f"{'─' * 18:<20} {'─' * 9:>10} {'─' * 9:>10} "
        f"{'─' * 9:>10}  {'─' * 10:<12} {'─' * 15}"
    )

    total_receipt = 0.0
    total_customer = 0.0
    total_match = 0.0
    count = 0

    for i, r in enumerate(txn_rows, 1):
        receipt = float(r['receipt_total'])
        cust_paid = float(r['customer_paid'])
        fam_match = float(r['fam_match'])
        total_receipt += receipt
        total_customer += cust_paid
        total_match += fam_match
        count += 1

        vendor = (r['vendor'][:18] + '..') if len(r['vendor']) > 20 else r['vendor']
        lines.append(
            f"  {i:<4} {r['fam_transaction_id']:<22} "
            f"{r['customer_id']:<12} {vendor:<20} "
            f"${receipt:>9.2f} ${cust_paid:>9.2f} "
            f"${fam_match:>9.2f}  {r['status']:<12} "
            f"{r['methods'] or ''}"
        )

    if fmnp_rows:
        lines.append("")
        lines.append("  --- FMNP (External) Entries ---")
        for r in fmnp_rows:
            count += 1
            amt = float(r['amount'])
            total_receipt += amt
            total_match += amt
            check_info = (
                f"FMNP – {r['check_count']} checks"
                if r['check_count'] else "FMNP (External)"
            )
            vendor = (r['vendor'][:18] + '..') if len(r['vendor']) > 20 else r['vendor']
            lines.append(
                f"  {count:<4} {'FMNP-' + str(r['id']):<22} "
                f"{'':<12} {vendor:<20} "
                f"${amt:>9.2f} ${'0.00':>9} "
                f"${amt:>9.2f}  {'FMNP Entry':<12} "
                f"{check_info}"
            )

    lines.append("")
    lines.append("-" * W)
    lines.append("  TOTALS")
    lines.append("-" * W)
    lines.append("")
    lines.append(f"  Total Receipts:      ${total_receipt:,.2f}")
    lines.append(f"  Customer Paid:       ${total_customer:,.2f}")
    lines.append(f"  FAM Match:           ${total_match:,.2f}")
    lines.append(f"  Transaction Count:   {count}")
    lines.append("")
    lines.append("=" * W)
    lines.append("  This file is automatically maintained by FAM Market Manager.")
    lines.append("  Open it with any text editor (Notepad, WordPad, etc.) to")
    lines.append("  review transactions if the application is unable to start.")
    lines.append("=" * W)
    lines.append("")

    # ── Write next to the database ────────────────────────────────
    backup_dir = os.path.dirname(os.path.abspath(get_db_path()))
    backup_path = os.path.join(backup_dir, "fam_ledger_backup.txt")
    with open(backup_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    logger.info("Ledger backup written: %s (%d transactions)", backup_path, count)
