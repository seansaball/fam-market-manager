"""Reports → Error Log filter "Errors Only" must include CRITICAL
entries (v2.0.2 fix).

Pre-v2.0.2 the level filter compared ``e['level'] == 'ERROR'`` and
silently dropped every CRITICAL entry.  Since the
``_global_exception_handler`` writes unhandled crashes at CRITICAL
(``fam/app.py:36``) and the v2.0.1 log_reader default level set
included CRITICAL, "Errors Only" was hiding exactly the failures
users care most about.  Cloud-side Error Log sync correctly
included CRITICAL, so before this fix local UI and cloud diverged
on the most important class of entry.

These tests pin the comparator fix.
"""

import pytest


def _filter_entries(entries, level_choice):
    """Mirror the level-filter logic in
    ``ReportsScreen._apply_error_log_filters`` so we can test it
    without instantiating Qt.  Keep this in sync with the actual
    code under review."""
    if level_choice == "errors":
        return [e for e in entries if e['level'] in ('ERROR', 'CRITICAL')]
    elif level_choice == "warnings":
        return [e for e in entries if e['level'] == 'WARNING']
    return list(entries)


@pytest.fixture
def sample_entries():
    return [
        {'level': 'CRITICAL', 'message': 'unhandled crash A'},
        {'level': 'ERROR', 'message': 'sync failure'},
        {'level': 'WARNING', 'message': 'photo dedup race'},
        {'level': 'CRITICAL', 'message': 'unhandled crash B'},
        {'level': 'ERROR', 'message': 'database lock'},
    ]


class TestErrorsOnlyIncludesCritical:

    def test_errors_only_includes_critical(self, sample_entries):
        result = _filter_entries(sample_entries, 'errors')
        levels = [e['level'] for e in result]
        assert 'CRITICAL' in levels, (
            "'Errors Only' filter must include CRITICAL entries — "
            "the global exception handler writes unhandled crashes "
            "at CRITICAL and excluding them defeats the v2.0.1 "
            "logging fix entirely.")

    def test_errors_only_includes_error(self, sample_entries):
        result = _filter_entries(sample_entries, 'errors')
        levels = [e['level'] for e in result]
        assert 'ERROR' in levels

    def test_errors_only_excludes_warnings(self, sample_entries):
        result = _filter_entries(sample_entries, 'errors')
        levels = [e['level'] for e in result]
        assert 'WARNING' not in levels

    def test_errors_only_returns_all_critical_and_error_entries(self, sample_entries):
        result = _filter_entries(sample_entries, 'errors')
        # Two CRITICAL + two ERROR = 4 entries; one WARNING dropped.
        assert len(result) == 4

    def test_warnings_only_excludes_critical(self, sample_entries):
        """The "Warnings Only" filter must NOT bleed CRITICAL into
        the warnings view (would be misleading)."""
        result = _filter_entries(sample_entries, 'warnings')
        levels = [e['level'] for e in result]
        assert 'CRITICAL' not in levels
        assert 'ERROR' not in levels
        assert all(level == 'WARNING' for level in levels)

    def test_both_filter_returns_all(self, sample_entries):
        """The default "Errors & Warnings" view returns everything
        since the upstream parser already excludes INFO/DEBUG."""
        result = _filter_entries(sample_entries, 'both')
        assert len(result) == len(sample_entries)


class TestProductionCodePathDirectly:
    """Pin the actual reports_screen source so future refactors
    can't silently revert the fix."""

    def test_filter_source_includes_critical(self):
        import inspect
        import fam.ui.reports_screen as rs
        src = inspect.getsource(rs._ReportsScreen if hasattr(rs, '_ReportsScreen')
                                 else rs.ReportsScreen)
        # The fix is the addition of 'CRITICAL' to the comparator.
        assert "'ERROR', 'CRITICAL'" in src or '"ERROR", "CRITICAL"' in src, (
            "ReportsScreen._apply_error_log_filters must include "
            "CRITICAL alongside ERROR for the 'Errors Only' filter")
