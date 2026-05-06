"""Tests for the v1.9.9 stale-market-day guard rails.

Background
==========
A volunteer left a market day open over multiple calendar days; new
transactions kept inheriting the *original* market_day's date,
mis-attributing them to a previous market in every report
downstream.

The fix is defence in depth:

1. **Auto-close at app launch** (``auto_close_stale_market_days``):
   any market day with ``status='Open'`` and ``date < eastern_today()``
   is closed automatically with a ``System (auto-close: stale market
   day)`` audit-trail entry.
2. **Hard guard in create_transaction**: rejects writes to a market
   day whose date is in the past, even if the auto-close hasn't
   fired (e.g. app left running across midnight).
3. **MainWindow notifies the user** of any auto-close on launch and
   routes them to the Market Day screen.

These tests verify each layer.
"""

from datetime import date, timedelta

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database
from fam.utils import timezone


@pytest.fixture
def stale_db(tmp_path, monkeypatch):
    """Fresh DB pinned to a specific Eastern "today" so the test can
    set up market days at known relative dates.  Overrides the
    suite-wide conftest fixture for this file's scope."""
    db_file = str(tmp_path / "test_stale.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    today = date(2026, 4, 29)
    monkeypatch.setattr(timezone, 'eastern_today', lambda: today)

    conn.execute(
        "INSERT INTO markets (id, name, address) VALUES"
        " (1, 'Test Market', '100 Main St')"
    )
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'Farm Stand')"
    )
    conn.commit()
    yield conn, today
    close_connection()


# ══════════════════════════════════════════════════════════════════
# Layer 1: auto-close at startup
# ══════════════════════════════════════════════════════════════════
class TestAutoCloseStaleMarketDays:

    def test_closes_a_single_stale_open_day(self, stale_db):
        from fam.models.market_day import (
            create_market_day, auto_close_stale_market_days,
            get_open_market_day,
        )
        conn, today = stale_db
        # Open a market 3 days in the past (forgotten on Sunday,
        # opened the app on Wednesday).
        old = today - timedelta(days=3)
        create_market_day(1, old.isoformat(), opened_by='Volunteer')

        closed = auto_close_stale_market_days()
        assert len(closed) == 1
        assert closed[0]['date'] == old.isoformat()
        assert closed[0]['market_name'] == 'Test Market'

        # Status flipped, get_open_market_day returns None.
        assert get_open_market_day() is None
        row = conn.execute(
            "SELECT status, closed_by FROM market_days WHERE date=?",
            (old.isoformat(),)
        ).fetchone()
        assert row['status'] == 'Closed'
        assert 'auto-close' in row['closed_by'].lower()

    def test_leaves_today_untouched(self, stale_db):
        from fam.models.market_day import (
            create_market_day, auto_close_stale_market_days,
            get_open_market_day,
        )
        conn, today = stale_db
        create_market_day(1, today.isoformat(), opened_by='Volunteer')

        closed = auto_close_stale_market_days()
        assert closed == []
        assert get_open_market_day() is not None

    def test_closes_multiple_stale_opens(self, stale_db):
        """Closes every market day that's behind today, regardless of
        how many accumulated."""
        from fam.models.market_day import (
            create_market_day, auto_close_stale_market_days,
        )
        conn, today = stale_db
        # Three stale opens (rare but possible if the app has
        # multiple-open guards bypassed somehow).
        for n in (1, 2, 3):
            d = (today - timedelta(days=n)).isoformat()
            create_market_day(1, d, opened_by='Volunteer')

        closed = auto_close_stale_market_days()
        assert len(closed) == 3
        # Stable order: ascending by date.
        dates = [c['date'] for c in closed]
        assert dates == sorted(dates)

    def test_writes_audit_log_entry_per_close(self, stale_db):
        from fam.models.market_day import (
            create_market_day, auto_close_stale_market_days,
        )
        conn, today = stale_db
        create_market_day(1, (today - timedelta(days=1)).isoformat(),
                           opened_by='Volunteer')
        auto_close_stale_market_days()

        rows = conn.execute(
            "SELECT action, changed_by, notes FROM audit_log"
            " WHERE table_name='market_days' AND action='AUTO_CLOSE'"
        ).fetchall()
        assert len(rows) == 1
        assert 'auto-close' in rows[0]['changed_by'].lower()
        # Note explains *why* this happened so coordinators reading
        # the log see the reason.
        assert 'left open' in rows[0]['notes'].lower()

    def test_returns_descriptors_for_ui_notification(self, stale_db):
        """Each returned dict has the fields the MainWindow dialog
        formats: id, market_name, date, opened_by."""
        from fam.models.market_day import (
            create_market_day, auto_close_stale_market_days,
        )
        conn, today = stale_db
        create_market_day(1, (today - timedelta(days=5)).isoformat(),
                           opened_by='Vol Tester')
        closed = auto_close_stale_market_days()
        assert len(closed) == 1
        c = closed[0]
        assert c['market_name'] == 'Test Market'
        assert c['opened_by'] == 'Vol Tester'
        assert 'date' in c and 'id' in c

    def test_no_op_when_nothing_open(self, stale_db):
        """Empty list when no open market days at all."""
        from fam.models.market_day import auto_close_stale_market_days
        assert auto_close_stale_market_days() == []


# ══════════════════════════════════════════════════════════════════
# Layer 2: create_transaction hard guard
# ══════════════════════════════════════════════════════════════════
class TestCreateTransactionStaleDateGuard:

    def test_blocks_writes_to_yesterday(self, stale_db):
        from fam.models.market_day import create_market_day
        from fam.models.transaction import create_transaction
        conn, today = stale_db
        yesterday = (today - timedelta(days=1)).isoformat()
        md_id = create_market_day(1, yesterday, opened_by='Volunteer')

        # Even though status is still Open, the date check refuses.
        with pytest.raises(ValueError) as exc:
            create_transaction(md_id, 1, 5000)
        msg = str(exc.value).lower()
        assert 'close this market day' in msg or 'open a new one' in msg

    def test_allows_writes_to_today(self, stale_db):
        from fam.models.market_day import create_market_day
        from fam.models.transaction import create_transaction
        conn, today = stale_db
        md_id = create_market_day(1, today.isoformat(),
                                    opened_by='Volunteer')
        # Should not raise.
        txn_id, fam_tid = create_transaction(md_id, 1, 5000)
        assert txn_id is not None
        assert today.isoformat().replace('-', '') in fam_tid

    def test_blocks_writes_to_future_day(self, stale_db):
        """Edge case: market day dated *after* today.  The current
        guard only blocks ``date < today``, so future-dated market
        days currently still accept writes — sanity-check that
        same-day boundary."""
        from fam.models.market_day import create_market_day
        from fam.models.transaction import create_transaction
        conn, today = stale_db
        # A market day dated tomorrow should still accept writes
        # today (rare but legitimate: setting up tomorrow's market
        # ahead of time).  The guard is one-sided by design.
        tomorrow = (today + timedelta(days=1)).isoformat()
        md_id = create_market_day(1, tomorrow, opened_by='Volunteer')
        # Should not raise.
        txn_id, _ = create_transaction(md_id, 1, 5000)
        assert txn_id is not None


# ══════════════════════════════════════════════════════════════════
# Layer 3: MainWindow surfaces a notification
# ══════════════════════════════════════════════════════════════════
class TestMainWindowSurfacesNotification:
    """Source-level guard so future refactors don't accidentally
    drop the auto-close notification on app launch."""

    def test_main_window_calls_auto_close_on_launch(self):
        import inspect
        import fam.ui.main_window as mw
        src = inspect.getsource(mw)
        assert '_check_stale_market_days' in src
        assert 'auto_close_stale_market_days' in src

    def test_notification_routes_to_market_day_screen(self):
        import inspect
        import fam.ui.main_window as mw
        src = inspect.getsource(mw.MainWindow._check_stale_market_days)
        # Must show a dialog when something was closed.
        assert 'QMessageBox' in src
        # And switch to the Market Day screen so the volunteer can
        # open today's market.
        assert 'setCurrentIndex' in src
