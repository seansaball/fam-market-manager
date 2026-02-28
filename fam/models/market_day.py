"""Market day CRUD operations."""

import logging
from datetime import datetime
from fam.database.connection import get_connection
from fam.models.audit import log_action

logger = logging.getLogger('fam.models.market_day')


def get_all_markets():
    """Return all markets (id, name) ordered by name."""
    conn = get_connection()
    rows = conn.execute("SELECT id, name FROM markets ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_all_market_days():
    conn = get_connection()
    rows = conn.execute("""
        SELECT md.*, m.name as market_name
        FROM market_days md
        JOIN markets m ON md.market_id = m.id
        ORDER BY md.date DESC, md.id DESC
    """).fetchall()
    return [dict(r) for r in rows]


def get_market_day_by_id(market_day_id):
    conn = get_connection()
    row = conn.execute("""
        SELECT md.*, m.name as market_name
        FROM market_days md
        JOIN markets m ON md.market_id = m.id
        WHERE md.id=?
    """, (market_day_id,)).fetchone()
    return dict(row) if row else None


def get_open_market_day():
    """Get the currently open market day, if any."""
    conn = get_connection()
    row = conn.execute("""
        SELECT md.*, m.name as market_name
        FROM market_days md
        JOIN markets m ON md.market_id = m.id
        WHERE md.status='Open'
        ORDER BY md.date DESC LIMIT 1
    """).fetchone()
    return dict(row) if row else None


def find_market_day(market_id, date_str):
    """Find an existing market day for the given market and date."""
    conn = get_connection()
    row = conn.execute("""
        SELECT md.*, m.name as market_name
        FROM market_days md
        JOIN markets m ON md.market_id = m.id
        WHERE md.market_id=? AND md.date=?
    """, (market_id, date_str)).fetchone()
    return dict(row) if row else None


def create_market_day(market_id, date_str, opened_by="System"):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO market_days (market_id, date, status, opened_by) VALUES (?, ?, 'Open', ?)",
        (market_id, date_str, opened_by)
    )
    conn.commit()
    md_id = cursor.lastrowid

    log_action('market_days', md_id, 'OPEN', opened_by,
               notes=f"Market day opened for market={market_id} date={date_str}")
    logger.info("Market day opened: id=%s market=%s date=%s by=%s",
                md_id, market_id, date_str, opened_by)
    return md_id


def close_market_day(market_day_id, closed_by="System"):
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE market_days SET status='Closed', closed_by=?, closed_at=? WHERE id=?",
        (closed_by, now, market_day_id)
    )
    conn.commit()

    log_action('market_days', market_day_id, 'CLOSE', closed_by,
               notes='Market day closed')
    logger.info("Market day closed: id=%s by=%s", market_day_id, closed_by)


def reopen_market_day(market_day_id, opened_by=None):
    conn = get_connection()
    if opened_by:
        conn.execute(
            "UPDATE market_days SET status='Open', opened_by=? WHERE id=?",
            (opened_by, market_day_id)
        )
    else:
        conn.execute(
            "UPDATE market_days SET status='Open' WHERE id=?",
            (market_day_id,)
        )
    conn.commit()

    who = opened_by or 'System'
    log_action('market_days', market_day_id, 'REOPEN', who,
               notes='Market day reopened')
    logger.info("Market day reopened: id=%s by=%s", market_day_id, who)


def get_market_day_transactions_summary(market_day_id):
    """Get summary of transactions for a market day."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT t.id, t.fam_transaction_id, t.receipt_total, t.status,
               v.name as vendor_name, t.created_at
        FROM transactions t
        JOIN vendors v ON t.vendor_id = v.id
        WHERE t.market_day_id = ?
        ORDER BY t.created_at DESC
    """, (market_day_id,)).fetchall()
    return [dict(r) for r in rows]
