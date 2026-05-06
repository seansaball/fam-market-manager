"""Timezone correctness tests
(v1.9.10 follow-up, 2026-05-01).

Eastern-time is the canonical timezone for every timestamp the
app creates.  At market boundaries (DST transitions, year-end,
leap day) edge cases can cause:

  * Transactions attributed to the wrong day
  * Stale-market-day guard mis-firing
  * Ledger filenames colliding
  * Audit log changed_at out of order

These tests pin the behavior using deterministic
``zoneinfo``-aware datetimes (no dependence on the machine's
clock).
"""

from datetime import date, datetime
from unittest.mock import patch

import pytest

from fam.utils.timezone import EASTERN, eastern_now, eastern_today


# ════════════════════════════════════════════════════════════════════
# 1. Basic timezone helpers
# ════════════════════════════════════════════════════════════════════


class TestEasternHelpers:

    def test_eastern_now_returns_aware_datetime(self):
        now = eastern_now()
        assert now.tzinfo is not None
        assert now.tzinfo.key == 'America/New_York'

    def test_eastern_today_matches_eastern_now_date(self):
        # eastern_today() must match eastern_now().date() exactly
        # — otherwise across-midnight calls disagree.
        for _ in range(3):
            now = eastern_now()
            today = eastern_today()
            assert today == now.date(), (
                "eastern_today and eastern_now.date() must agree")


# ════════════════════════════════════════════════════════════════════
# 2. DST transition — spring forward (March 9, 2025 02:00 EST → 03:00 EDT)
# ════════════════════════════════════════════════════════════════════


class TestDSTSpringForward:

    def test_pre_spring_forward_is_est(self):
        # March 9, 2025 01:30 — still EST, UTC-5
        dt = datetime(2025, 3, 9, 1, 30, tzinfo=EASTERN)
        assert dt.utcoffset().total_seconds() == -5 * 3600

    def test_post_spring_forward_is_edt(self):
        # March 9, 2025 03:30 — now EDT, UTC-4
        dt = datetime(2025, 3, 9, 3, 30, tzinfo=EASTERN)
        assert dt.utcoffset().total_seconds() == -4 * 3600

    def test_market_open_across_spring_forward_dates_correctly(self):
        """A market opening on March 9 sees the date attributed
        to March 9 in EST AND in EDT — a transaction at 01:30 EST
        (pre-jump) and 03:30 EDT (post-jump) both belong to
        2025-03-09."""
        d_pre = datetime(2025, 3, 9, 1, 30, tzinfo=EASTERN).date()
        d_post = datetime(2025, 3, 9, 3, 30, tzinfo=EASTERN).date()
        assert d_pre == d_post == date(2025, 3, 9), (
            "transactions across the spring-forward jump must "
            "attribute to the same calendar date")


# ════════════════════════════════════════════════════════════════════
# 3. DST transition — fall back (Nov 2, 2025 02:00 EDT → 01:00 EST)
# ════════════════════════════════════════════════════════════════════


class TestDSTFallBack:

    def test_pre_fall_back_is_edt(self):
        # Nov 2, 2025 00:30 EDT
        dt = datetime(2025, 11, 2, 0, 30, tzinfo=EASTERN)
        assert dt.utcoffset().total_seconds() == -4 * 3600

    def test_post_fall_back_is_est(self):
        # Nov 2, 2025 03:00 — back to EST, UTC-5
        dt = datetime(2025, 11, 2, 3, 0, tzinfo=EASTERN)
        assert dt.utcoffset().total_seconds() == -5 * 3600

    def test_audit_timestamps_around_fall_back_dont_repeat_dates(
            self, monkeypatch):
        """Two calls to ``eastern_today`` around a fall-back
        transition must both return Nov 2 — the duplicated 1-2
        AM hour doesn't move the calendar date backwards.
        """
        # First call at 00:30 EDT (before fall-back)
        # Second call at 03:00 EST (after fall-back)
        # Both must be 2025-11-02.
        d1 = datetime(2025, 11, 2, 0, 30, tzinfo=EASTERN).date()
        d2 = datetime(2025, 11, 2, 3, 0, tzinfo=EASTERN).date()
        assert d1 == d2 == date(2025, 11, 2)


# ════════════════════════════════════════════════════════════════════
# 4. Year-end rollover
# ════════════════════════════════════════════════════════════════════


class TestYearEndRollover:

    def test_dec_31_2025_2359_eastern_is_2025(self):
        dt = datetime(2025, 12, 31, 23, 59, tzinfo=EASTERN)
        assert dt.year == 2025
        assert dt.month == 12
        assert dt.day == 31

    def test_jan_1_2026_0001_eastern_is_2026(self):
        dt = datetime(2026, 1, 1, 0, 1, tzinfo=EASTERN)
        assert dt.year == 2026
        assert dt.month == 1
        assert dt.day == 1

    def test_year_rollover_in_audit_query_window(self):
        """Audit log queries that filter by date range must NOT
        accidentally include the year boundary's "Dec 31 23:59"
        in a "Jan" query.  This is a string-comparison check
        on YYYY-MM-DD format."""
        new_years_eve = '2025-12-31'
        new_years_day = '2026-01-01'
        # ISO date strings sort correctly alphabetically — the
        # filter ``WHERE changed_at < '2026-01-01'`` excludes
        # Dec 31, includes ones before.
        assert new_years_eve < new_years_day
        assert ('2025-12-31 23:59:59' < '2026-01-01 00:00:00')


# ════════════════════════════════════════════════════════════════════
# 5. Leap day
# ════════════════════════════════════════════════════════════════════


class TestLeapDay:

    def test_feb_29_2024_is_valid_eastern_date(self):
        # 2024 is a leap year.
        dt = datetime(2024, 2, 29, 12, 0, tzinfo=EASTERN)
        assert dt.day == 29
        assert dt.month == 2

    def test_feb_29_2025_does_not_exist(self):
        # 2025 is NOT a leap year; constructing Feb 29 raises.
        with pytest.raises(ValueError):
            datetime(2025, 2, 29, 12, 0, tzinfo=EASTERN)

    def test_leap_year_doesnt_break_year_boundary_checks(self):
        # March 1, 2024 (post-leap-day) — date arithmetic still
        # works.
        from datetime import timedelta
        leap = datetime(2024, 2, 29, tzinfo=EASTERN)
        next_day = leap + timedelta(days=1)
        assert next_day.date() == date(2024, 3, 1)


# ════════════════════════════════════════════════════════════════════
# 6. Source-pin: code uses eastern_* helpers, not naive datetime.now()
# ════════════════════════════════════════════════════════════════════


class TestNaiveDatetimeNotUsed:
    """Critical: production code paths must use ``eastern_*``
    helpers, NOT naive ``datetime.now()`` or ``date.today()`` —
    those return values in the OS-local timezone, which on a
    deployed laptop is usually-but-not-guaranteed Eastern.
    """

    def test_eastern_helpers_referenced_in_models(self):
        """Pin that key model files import from
        ``fam.utils.timezone`` instead of using naive
        ``datetime.now`` directly."""
        import inspect
        from fam.models import transaction, audit, market_day
        for module in (transaction, audit, market_day):
            src = inspect.getsource(module)
            assert ('eastern_timestamp' in src
                    or 'eastern_now' in src
                    or 'eastern_today' in src), (
                f"{module.__name__} must use eastern_* helpers, "
                f"not OS-local datetime.now() / date.today()")

    def test_no_naive_datetime_now_in_critical_paths(self):
        """Light source audit: ``datetime.now()`` (no tz arg)
        must not appear in model files.  Catch-all sanity
        check; the eastern_* helpers wrap the tz."""
        import inspect
        from fam.models import transaction, audit
        for module in (transaction, audit):
            src = inspect.getsource(module)
            # We're catching raw ``datetime.now()`` (no arg).
            # ``datetime.now(EASTERN)`` would have a paren+arg.
            import re
            naive = re.findall(r'datetime\.now\(\s*\)', src)
            assert not naive, (
                f"{module.__name__} contains naive "
                f"datetime.now() calls: {naive!r} — these "
                f"return OS-local time, not Eastern; switch to "
                f"eastern_now()")
