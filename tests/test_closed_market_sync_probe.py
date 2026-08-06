"""Periodic sync probe keeps running after the market day closes
(v2.1.2).

Reported case (BPFM): a market runs on-site with no internet, closes,
and the coordinator enters/reconciles data the NEXT day on a connected
laptop.  Previously the periodic sync timer was gated on an OPEN market
day, so it stopped at close and pending data sat local until a manual
"Sync to Cloud".  Now the probe runs whenever periodic sync is enabled,
regardless of open/closed — ``_trigger_sync`` already collects all
market days when none is open, so the closed day's data flushes once
the network returns.
"""

import pytest

from fam.database.connection import (
    close_connection, get_connection, set_db_path,
)
from fam.database.schema import initialize_database
from fam.database.seed import seed_sample_data


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    close_connection()
    set_db_path(str(tmp_path / "closed_probe.db"))
    initialize_database()
    seed_sample_data()
    yield
    close_connection()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class TestClosedMarketSyncProbe:

    def _window(self):
        from fam.ui.main_window import MainWindow
        return MainWindow()

    def test_timer_runs_when_no_open_day_and_periodic_on(self, qapp):
        """The reported fix: periodic sync ON, no market day open →
        the probe timer stays running (was stopped pre-v2.1.2)."""
        from fam.utils.app_settings import set_setting
        from fam.models.market_day import get_open_market_day
        set_setting('sync_periodic', '1')
        w = self._window()
        assert get_open_market_day() is None
        w._update_sync_timer()
        assert w._sync_timer.isActive() is True

    def test_timer_stops_when_periodic_off(self, qapp):
        """The setting is still respected — off means no probe."""
        from fam.utils.app_settings import set_setting
        set_setting('sync_periodic', '0')
        w = self._window()
        w._update_sync_timer()
        assert w._sync_timer.isActive() is False

    def test_trigger_sync_full_scope_when_no_open_day(self, qapp, monkeypatch):
        """When the probe fires with no open day, the sync is collected
        at FULL scope (market_day_id=None) so ALL days — including the
        just-closed one — are flushed.  This is what makes the closed
        probe actually push pending data."""
        from fam.models.market_day import get_open_market_day
        assert get_open_market_day() is None
        w = self._window()

        captured = {}

        # Configure the backend so _trigger_sync proceeds, then capture
        # the scope the SyncWorker is constructed with.  _trigger_sync
        # imports GoogleSheetsBackend and SyncWorker locally from their
        # source modules, so patch there.
        import fam.sync.gsheets as gs
        monkeypatch.setattr(
            gs.GoogleSheetsBackend, "is_configured", lambda self: True)

        import fam.sync.worker as worker_mod

        class _CapturingWorker:
            def __init__(self, manager, market_day_id=None):
                captured['scope'] = market_day_id
            def moveToThread(self, *a, **k):
                pass
            def run(self):
                pass

        monkeypatch.setattr(worker_mod, "SyncWorker", _CapturingWorker)
        # Also patch the name imported into main_window's _trigger_sync
        # scope (it imports SyncWorker locally inside the method).
        w._trigger_sync()
        assert captured.get('scope') is None, (
            "With no open market day, the probe must collect ALL days "
            "(scope=None), not a narrowed scope.")
