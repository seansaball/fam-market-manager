"""Market day CRUD operations."""

import logging
from fam.database.connection import get_connection
from fam.utils.timezone import eastern_timestamp
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
        "INSERT INTO market_days (market_id, date, status, opened_by, created_at) VALUES (?, ?, 'Open', ?, ?)",
        (market_id, date_str, opened_by, eastern_timestamp())
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
    now = eastern_timestamp()
    conn.execute(
        "UPDATE market_days SET status='Closed', closed_by=?, closed_at=? WHERE id=?",
        (closed_by, now, market_day_id)
    )
    conn.commit()

    log_action('market_days', market_day_id, 'CLOSE', closed_by,
               notes='Market day closed')
    logger.info("Market day closed: id=%s by=%s", market_day_id, closed_by)

    # Multi-year scaling hygiene (v1.9.10 follow-up, 2026-05-01):
    # Run ANALYZE on close to refresh SQLite's query-plan
    # statistics now that the day's writes are settled.  Without
    # periodic ANALYZE, plans drift as tables grow and a Year-3
    # ``audit_log`` query that used a covering index in Year-1
    # can degrade to a full scan.
    #
    # ANALYZE is fast (a few hundred ms even at 500K rows) because
    # SQLite samples instead of full-scanning.  Wrapped in
    # try/except — query-plan health is advisory, never blocks the
    # close from succeeding.
    try:
        conn.execute("ANALYZE")
        # VACUUM is too expensive to run synchronously on every
        # close (rewrites the entire DB file).  Defer it to the
        # quarterly maintenance window or a manual operator
        # action.  We DO log a hint when the page-fragmentation
        # ratio looks unhealthy so the operator gets a nudge.
        try:
            free_pages = conn.execute(
                "PRAGMA freelist_count").fetchone()[0]
            total_pages = conn.execute(
                "PRAGMA page_count").fetchone()[0]
            # Size-aware threshold (v2.0.6, 2026-05-06):
            #
            # The 30% page-free ratio is meaningful at production
            # scale where it represents tens-to-hundreds of MB of
            # unreclaimed disk space.  At small DB sizes the ratio
            # is misleading: a freshly-reset 73-page (~292KB) DB
            # routinely shows 30%+ free pages because SQLite keeps
            # the file's allocated pages even after a wipe — we
            # were warning operators about 88KB of "fragmentation"
            # that's completely meaningless.
            #
            # Gate on BOTH a meaningful absolute size (> 4MB) AND
            # the percentage threshold so the hint only fires when
            # there's actually something to gain from a VACUUM.
            # 1000 pages × 4KB default page size = 4MB minimum;
            # at that point a 30% free ratio is at least 1.2MB of
            # reclaimable space, which starts to be worth the
            # maintenance-window cost of a VACUUM.
            MIN_PAGES_FOR_FRAG_HINT = 1000
            if (total_pages > MIN_PAGES_FOR_FRAG_HINT
                    and free_pages / total_pages > 0.30):
                frag = free_pages / total_pages
                logger.warning(
                    "DB fragmentation %.1f%% (free %d / total %d "
                    "pages) — consider running VACUUM in a "
                    "maintenance window",
                    frag * 100, free_pages, total_pages)
        except Exception:
            pass
    except Exception:
        logger.exception(
            "ANALYZE on market close failed — non-fatal, plan "
            "stats may stale")


def auto_close_stale_market_days() -> list[dict]:
    """Auto-close any market day left ``Open`` with a date before today.

    Reproduces the v1.9.9 onsite finding: a volunteer left a market
    day open over multiple calendar days, and subsequent transactions
    inherited the *original* market_day's date — corrupting the date
    attribution on every report downstream.

    Called once at app startup (after schema init).  Strictly
    compares ``date < eastern_today()`` so a market still running at
    11:59 PM on its own day is untouched; only days that have rolled
    past their own calendar date get closed.

    Each auto-close:
      * sets status='Closed', closed_by='System (auto-close: stale)',
        closed_at = current eastern timestamp
      * emits an audit_log row with a clear "auto-close" note
      * logs an INFO line so coordinators reviewing the file log can
        see exactly what happened and when

    Returns
    -------
    list[dict]
        One dict per closed day, with keys ``id``, ``market_id``,
        ``market_name``, ``date``, ``opened_by``, ``opened_at`` —
        enough for the UI to show a friendly "this is what we
        closed" notification at next launch.  Empty list when no
        stale days were found (the typical case).
    """
    from fam.utils.timezone import eastern_today
    today_iso = eastern_today().isoformat()

    conn = get_connection()
    rows = conn.execute(
        """SELECT md.id, md.market_id, md.date, md.opened_by, md.created_at,
                  m.name AS market_name
             FROM market_days md
             JOIN markets m ON m.id = md.market_id
            WHERE md.status = 'Open' AND md.date < ?
         ORDER BY md.date""",
        (today_iso,)
    ).fetchall()
    if not rows:
        return []

    closed_by_label = 'System (auto-close: stale market day)'
    now = eastern_timestamp()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        conn.execute(
            "UPDATE market_days SET status='Closed', closed_by=?,"
            " closed_at=? WHERE id=?",
            (closed_by_label, now, d['id'])
        )
        log_action(
            'market_days', d['id'], 'AUTO_CLOSE', closed_by_label,
            notes=(f"Auto-closed at {now} — market day from "
                   f"{d['date']} had been left open past its own "
                   f"calendar date.  This prevents new transactions "
                   f"from being mis-dated to a previous market.")
        )
        logger.warning(
            "Auto-closed stale market day: id=%s market='%s' "
            "originally_opened=%s for date=%s",
            d['id'], d['market_name'], d['created_at'], d['date'],
        )
        out.append({
            'id': d['id'],
            'market_id': d['market_id'],
            'market_name': d['market_name'],
            'date': d['date'],
            'opened_by': d['opened_by'],
            'opened_at': d['created_at'],
        })
    conn.commit()
    return out


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
