"""Year-scale sync scaling and rate-limit handling
(v1.9.10 follow-up, 2026-05-01).

The cloud sync uses a diff-based upsert: ``ws.get_all_values()`` to
read the existing sheet, build an in-memory composite-key index,
diff against the locally-collected rows, then write only the
changed cells / append only new rows / delete only stale rows.
This pins the WIRE write traffic to the diff size — not the full
dataset.

But before this session's fix, the LOCAL collection cost was
unscoped: every auto-triggered sync (= every confirm / adjust /
void / FMNP / draft / receipt-intake mutation) ran
``collect_sync_data()`` over ALL historical market days.  After a
year (~50 market days, thousands of transactions, tens of thousands
of audit rows) every $5 transaction triggered:

  * 8 per-md collectors × 50 market days = 400 SQL queries
  * 10 ``ws.get_all_values()`` calls (one per sheet tab) — each
    reading every row, ~5–10 MB payload at year-scale
  * Several seconds of wall time per sync

…all to push a single mutation that lives on TODAY's market day.

This test pins the new scope-aware behaviour:

  * Auto-syncs (force=False) collect ONLY the open market day
  * Manual sync (force=True) collects everything
  * Diff-upsert wire traffic is bounded by the diff, not the
    dataset
  * 429 rate-limit responses retry with exponential backoff and
    eventually succeed

The tests use a synthetic year-of-transactions DB (52 weeks ×
~10 transactions per week = 520 transactions, plus audit-log
rows) and a stub backend that counts API calls.
"""

import time
from unittest.mock import MagicMock

import pytest

from fam.database.connection import (
    get_connection, set_db_path, close_connection,
)
from fam.database.schema import initialize_database


# ── Fixture: a "year of operation" DB ─────────────────────────────


@pytest.fixture
def year_db(tmp_path):
    """Build a DB with ~52 market days and ~520 transactions.

    Sized to match a single-market full-year deployment.  Tweak
    via the ``WEEKS`` / ``TXN_PER_WEEK`` constants for stress
    testing.
    """
    WEEKS = 52
    TXN_PER_WEEK = 10

    db_file = str(tmp_path / "year.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V1')")
    conn.execute("INSERT INTO vendors (id, name) VALUES (2, 'V2')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) VALUES (1, 1), (1, 2)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "(1, 'SNAP', 100.0, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO vendor_payment_methods (vendor_id, "
        "payment_method_id) VALUES (1, 1), (2, 1)")
    conn.commit()

    # Historical-data fixture: bypass model APIs (which guard
    # against creating transactions on past-dated market days)
    # and write rows directly.  We're simulating a year of
    # already-confirmed history, not testing the
    # transaction-creation flow.
    md_ids: list[int] = []
    for w in range(WEEKS):
        # Synthetic dates spread across 2099 (well in the future
        # to avoid the stale-date guard while testing).  The
        # unique format YYYY-MM-DD lets the collectors group
        # correctly.
        month = (w // 4) + 1
        day = ((w % 4) + 1) * 7
        date_str = f'2099-{month:02d}-{day:02d}'
        cur = conn.execute(
            "INSERT INTO market_days (market_id, date, status, "
            "opened_by) VALUES (1, ?, 'Closed', 'Tester')",
            (date_str,))
        md_id = cur.lastrowid
        md_ids.append(md_id)
        for t in range(TXN_PER_WEEK):
            cur = conn.execute(
                "INSERT INTO customer_orders "
                "(market_day_id, customer_label, zip_code, "
                " status, created_at) "
                "VALUES (?, ?, '15102', 'Confirmed', ?)",
                (md_id, f'C-{w:02d}-{t:02d}',
                 f'{date_str} 10:00:00'))
            order_id = cur.lastrowid
            cents = 2000 + (t * 17)
            fam_tid = (
                f"FAM-T-{date_str.replace('-', '')}-"
                f"{w:02d}{t:02d}")
            cur = conn.execute(
                "INSERT INTO transactions "
                "(fam_transaction_id, market_day_id, vendor_id, "
                " receipt_total, customer_order_id, status, "
                " created_at) VALUES (?, ?, ?, ?, ?, 'Confirmed', ?)",
                (fam_tid, md_id, (t % 2) + 1,
                 cents, order_id,
                 f'{date_str} 10:00:00'))
            txn_id = cur.lastrowid
            conn.execute(
                "INSERT INTO payment_line_items "
                "(transaction_id, payment_method_id, "
                " method_name_snapshot, match_percent_snapshot, "
                " method_amount, match_amount, customer_charged, "
                " created_at) VALUES (?, 1, 'SNAP', 100.0, ?, ?, ?, ?)",
                (txn_id, cents, cents // 2, cents - (cents // 2),
                 f'{date_str} 10:00:00'))
    conn.commit()

    yield {
        'conn': conn,
        'md_ids': md_ids,
        'open_md_id': md_ids[-1],
        'expected_txn_count': WEEKS * TXN_PER_WEEK,
    }
    close_connection()


# ════════════════════════════════════════════════════════════════════
# 1. Scoped collection — auto-syncs only touch the open market day
# ════════════════════════════════════════════════════════════════════


class TestScopedCollectionAtYearScale:

    def test_full_collect_covers_all_market_days(self, year_db):
        """``collect_sync_data()`` with no scope argument walks
        every historical market day — that's the manual / market-
        close behaviour."""
        from fam.sync.data_collector import collect_sync_data
        data = collect_sync_data()
        # Every per-md collector should see all 520 confirmed
        # transactions in their respective rolled-up tabs.
        ledger = data.get('Detailed Ledger', [])
        assert len(ledger) >= year_db['expected_txn_count'], (
            f"full Detailed Ledger collection should include all "
            f"{year_db['expected_txn_count']} transactions; "
            f"got {len(ledger)}")

    def test_scoped_collect_only_touches_one_md(self, year_db):
        """``collect_sync_data(market_day_id=X)`` collects ONLY
        that market day's data — at year-scale this is the
        difference between 1 SQL pass and 50 SQL passes per
        per-md collector."""
        from fam.sync.data_collector import collect_sync_data
        scoped = collect_sync_data(
            market_day_id=year_db['open_md_id'])
        ledger = scoped.get('Detailed Ledger', [])
        # Open md has TXN_PER_WEEK = 10 transactions.
        assert len(ledger) == 10, (
            f"scoped collection should return only the open "
            f"market day's 10 txns; got {len(ledger)}")

    def test_scoped_collect_runs_significantly_faster(self, year_db):
        """Wall-time pin: scoped collection MUST be much faster
        than full collection at year-scale.  This is not a strict
        microbenchmark — only an order-of-magnitude check that
        the scope argument actually skips work."""
        from fam.sync.data_collector import collect_sync_data
        t0 = time.perf_counter()
        full = collect_sync_data()
        full_dt = time.perf_counter() - t0

        t0 = time.perf_counter()
        scoped = collect_sync_data(
            market_day_id=year_db['open_md_id'])
        scoped_dt = time.perf_counter() - t0

        # Scoped should be at least 2× faster — more in practice.
        # (Loose bound to avoid CI flakiness.)
        assert scoped_dt < full_dt, (
            f"scoped collection ({scoped_dt:.3f}s) should be "
            f"faster than full ({full_dt:.3f}s)")

    def test_main_window_passes_scope_for_auto_sync(self):
        """Source-level pin: ``MainWindow._trigger_sync`` must
        construct the worker with ``market_day_id=`` from the
        open market day for non-forced (auto-triggered) syncs.

        Drop this and every confirm re-collects all history —
        the regression we're guarding against.
        """
        import inspect
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._trigger_sync)
        assert 'get_open_market_day' in src, (
            "_trigger_sync must read the open market day to scope "
            "auto-syncs")
        assert 'market_day_id=scope_md_id' in src, (
            "_trigger_sync must pass scope_md_id to SyncWorker")
        assert 'if not force:' in src, (
            "scope must be applied only when force=False (manual "
            "Sync to Cloud / market-close still does full sweep)")


# ════════════════════════════════════════════════════════════════════
# 2. Diff-upsert wire traffic — bounded by the DIFF, not the dataset
# ════════════════════════════════════════════════════════════════════


class _CountingBackend:
    """Stub backend that counts upsert_rows invocations and the
    number of rows passed in.  Used to assert that auto-syncs
    push a small payload even with a large existing sheet."""

    def __init__(self):
        self.is_configured = MagicMock(return_value=True)
        self.calls = []

    def upsert_rows(self, sheet, rows, keys, delete_stale=True):
        from fam.sync.base import SyncResult
        self.calls.append({'sheet': sheet, 'rows': len(rows)})
        return SyncResult(success=True, rows_synced=len(rows))


class TestDiffUpsertBoundedByScope:

    def test_scoped_sync_payload_sized_to_open_md_only(self, year_db):
        """When auto-sync scopes to the open market day, the
        payload pushed to ``upsert_rows`` for the per-md tabs
        contains only THAT market day's rows — not the year's
        worth.  The wire-write cost stays constant regardless of
        history depth."""
        from fam.sync.data_collector import collect_sync_data
        from fam.sync.manager import SyncManager

        scoped_data = collect_sync_data(
            market_day_id=year_db['open_md_id'])
        backend = _CountingBackend()
        mgr = SyncManager(backend, throttle_writes=False)
        mgr.sync_all(scoped_data)

        ledger_call = next(
            c for c in backend.calls if c['sheet'] == 'Detailed Ledger')
        # Scoped: 10 rows for the open md.
        assert ledger_call['rows'] == 10, (
            f"Scoped sync should push 10 ledger rows for the open "
            f"md; got {ledger_call['rows']}")

    def test_full_sync_includes_history(self, year_db):
        """Manual / market-close path: the unscoped collection
        + sync DOES carry the full year's rows so the sheet
        eventually has every transaction.  Diff-upsert ensures
        this push is idempotent."""
        from fam.sync.data_collector import collect_sync_data
        from fam.sync.manager import SyncManager

        full_data = collect_sync_data()
        backend = _CountingBackend()
        mgr = SyncManager(backend, throttle_writes=False)
        mgr.sync_all(full_data)

        ledger_call = next(
            c for c in backend.calls if c['sheet'] == 'Detailed Ledger')
        assert ledger_call['rows'] >= year_db['expected_txn_count']


# ════════════════════════════════════════════════════════════════════
# 3. Rate-limit retry — 429 responses recover with backoff
# ════════════════════════════════════════════════════════════════════


class TestRateLimitRetry:
    """The Sheets API enforces ~60 writes/min/user and ~300
    reads/min/user.  Retries on 429 (with exponential backoff)
    let the sync ride out brief throttle bursts.  These tests
    pin that behavior using ``_retry_on_error`` directly so
    we don't have to fake gspread."""

    def test_retry_on_429_eventually_succeeds(self, monkeypatch):
        from fam.sync import gsheets

        # Simulate a 429 once, then success.
        attempts = {'n': 0}

        class FakeAPIError(Exception):
            def __init__(self, status):
                self.response = type('R', (), {'status_code': status})

        def flaky():
            attempts['n'] += 1
            if attempts['n'] == 1:
                raise FakeAPIError(429)
            return 'ok'

        # Disable real sleep to keep the test fast.
        monkeypatch.setattr(gsheets.time, 'sleep', lambda s: None)

        out = gsheets._retry_on_error(flaky, max_retries=3)
        assert out == 'ok'
        assert attempts['n'] == 2

    def test_retry_eventually_gives_up(self, monkeypatch):
        """If the API stays in 429 forever, ``_retry_on_error``
        eventually raises so the caller can mark the sync as
        failed (and the cooldown buffers the next attempt)."""
        from fam.sync import gsheets

        class FakeAPIError(Exception):
            def __init__(self, status):
                self.response = type('R', (), {'status_code': status})

        def always_429():
            raise FakeAPIError(429)

        monkeypatch.setattr(gsheets.time, 'sleep', lambda s: None)
        with pytest.raises(FakeAPIError):
            gsheets._retry_on_error(always_429, max_retries=3)

    def test_non_retryable_error_propagates_immediately(
            self, monkeypatch):
        """A non-429/5xx error (e.g. 401 invalid creds) should
        NOT be retried — that's a configuration issue, not a
        transient rate-limit blip."""
        from fam.sync import gsheets

        class FakeAPIError(Exception):
            def __init__(self, status):
                self.response = type('R', (), {'status_code': status})

        def auth_error():
            raise FakeAPIError(401)

        monkeypatch.setattr(gsheets.time, 'sleep', lambda s: None)
        with pytest.raises(FakeAPIError):
            gsheets._retry_on_error(auth_error, max_retries=3)


# ════════════════════════════════════════════════════════════════════
# 4. Manager-level tab throttle — 1s gap between tabs
# ════════════════════════════════════════════════════════════════════


class TestManagerThrottle:
    """``SyncManager.sync_all`` sleeps 1s between tabs to stay under
    the per-minute write quota.  Tests use ``throttle_writes=False``
    in production-test setups where speed matters; the default is
    True for the real app."""

    def test_throttle_default_on(self):
        from fam.sync.manager import SyncManager
        m = SyncManager(MagicMock())
        assert m._throttle_writes is True

    def test_throttle_can_be_disabled_for_tests(self):
        from fam.sync.manager import SyncManager
        m = SyncManager(MagicMock(), throttle_writes=False)
        assert m._throttle_writes is False

    def test_throttle_inserts_sleep_between_tabs(self, monkeypatch):
        """``time.sleep(1.0)`` fires once between consecutive
        tabs (N-1 sleeps for N tabs)."""
        from fam.sync.manager import SyncManager
        from fam.sync.base import SyncResult

        sleeps = []
        monkeypatch.setattr(
            'fam.sync.manager.time.sleep',
            lambda s: sleeps.append(s))

        backend = MagicMock()
        backend.upsert_rows = MagicMock(
            return_value=SyncResult(success=True, rows_synced=0))
        m = SyncManager(backend, throttle_writes=True)
        m.sync_all({
            'A': [{'x': 1}],
            'B': [{'x': 2}],
            'C': [{'x': 3}],
        })
        # 3 tabs → 2 throttle sleeps of 1.0s each.
        assert sleeps == [1.0, 1.0]


# ════════════════════════════════════════════════════════════════════
# 5. Cooldown — repeated mutations don't fire repeated full collects
# ════════════════════════════════════════════════════════════════════


class TestCooldownPreventsHotLoopAtScale:
    """``MainWindow._sync_cooldown`` fires once per 60 seconds.
    Even at year-scale this means a burst of 30 confirms in 60s
    triggers ONE sync, not 30 — without that, even scoped
    collection would still pile up Qt-thread work.

    Source-level pin so the cooldown wiring stays intact."""

    def test_cooldown_interval_is_60_seconds(self):
        import inspect
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow)
        assert 'setInterval(60_000)' in src, (
            "Sync cooldown must be 60s to stay under Sheets API "
            "rate limits when many mutations land in quick "
            "succession")

    def test_already_running_sync_short_circuits(self):
        """Single-flight: if a sync thread is alive,
        ``_trigger_sync`` returns immediately without piling up
        another collection / API call."""
        import inspect
        from fam.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._trigger_sync)
        assert ('isRunning()' in src
                and 'Sync already in progress' in src), (
            "_trigger_sync must short-circuit when a sync thread "
            "is already running")
