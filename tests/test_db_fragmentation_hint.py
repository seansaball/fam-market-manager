"""DB fragmentation hint is size-aware (v2.0.6 fix).

User-reported (2026-05-06): a freshly-reset DB (~292KB / 73 pages,
22 free) tripped the WARNING because the page-free ratio was 30.1%.
That percentage looks alarming but represents 88KB of unreclaimed
space — completely meaningless.  The advisory was calibrated for
production-scale DBs (hundreds of MB) where 30% fragmentation
implies real disk waste; at small sizes it just spams the Error Log.

Fix: gate the warning on BOTH a meaningful absolute size (> 1000
pages, ~4MB at default 4KB page size) AND the 30% percentage
threshold.
"""

import logging

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_frag.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield tmp_path
    close_connection()


def _setup_market_day(conn, status='Open'):
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit) "
        "VALUES (1, 'M', 10000)")
    conn.execute(
        "INSERT INTO market_days "
        "(id, market_id, date, status, opened_by) "
        "VALUES (1, 1, '2026-05-06', ?, 'T')", (status,))
    conn.commit()


class TestFragHintSuppressedOnSmallDb:
    """Small DBs (< 4MB / 1000 pages) must NOT trigger the
    fragmentation warning even when the page-free ratio is high."""

    def test_close_market_day_does_not_warn_on_small_db(
            self, caplog):
        from fam.models.market_day import close_market_day

        conn = get_connection()
        _setup_market_day(conn)

        # Force a high free-page ratio by inserting + deleting
        # rows.  But a freshly-initialised test DB is already
        # under the 1000-page floor, so the warning should be
        # suppressed regardless.
        conn.execute(
            "INSERT INTO markets (name, daily_match_limit) "
            "VALUES ('A', 0), ('B', 0), ('C', 0), ('D', 0)")
        conn.execute(
            "DELETE FROM markets WHERE id > 1")
        conn.commit()

        with caplog.at_level(
                logging.WARNING, logger='fam.models.market_day'):
            close_market_day(1, closed_by='T')

        # Filter for the specific fragmentation warning text
        frag_msgs = [
            r for r in caplog.records
            if 'DB fragmentation' in r.getMessage()]
        assert frag_msgs == [], (
            f"Small DBs (< 1000 pages) must not trigger the "
            f"fragmentation warning.  Got: "
            f"{[r.getMessage() for r in frag_msgs]}.  Pre-fix the "
            f"warning fired at 30.1% free-page ratio on a 292KB "
            f"DB, which represents ~88KB of unreclaimed space — "
            f"meaningless.  The hint is for ops at production "
            f"scale where fragmentation actually matters.")


class TestFragHintFiresWhenSignificant:
    """When the gate IS satisfied (large DB + high ratio), the
    warning still fires.  We can't easily build a 4MB DB in a unit
    test, so verify the threshold values in the source directly."""

    def test_threshold_constant_is_at_least_1000_pages(self):
        import inspect
        from fam.models import market_day as md
        # Find close_market_day's source
        src = inspect.getsource(md.close_market_day)
        assert 'MIN_PAGES_FOR_FRAG_HINT' in src, (
            "close_market_day should define a named constant for "
            "the minimum-pages threshold so the gate is obvious "
            "in the source.")
        assert 'total_pages > MIN_PAGES_FOR_FRAG_HINT' in src, (
            "The fragmentation gate must check total_pages exceeds "
            "the minimum-pages threshold BEFORE evaluating the "
            "percentage.  Without this gate, small DBs (post-reset, "
            "fresh installs) trip the warning on meaningless "
            "absolute sizes.")
        # Inspect the actual value of the constant by exec-ing the
        # function up to the assignment.  Simpler: search for the
        # literal in source.
        import re
        match = re.search(
            r'MIN_PAGES_FOR_FRAG_HINT\s*=\s*(\d+)', src)
        assert match is not None
        threshold = int(match.group(1))
        assert threshold >= 1000, (
            f"Minimum-pages threshold should be at least 1000 "
            f"(~4MB at default 4KB page size).  Got {threshold}.")
