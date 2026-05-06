"""gsheets._retry_on_error 4xx guard (v2.0.2 fix).

Same bug-shape as the v2.0.1 drive.py fix: ``requests.HTTPError``
inherits from ``OSError`` (via ``IOError`` aliasing in the requests
package), so the ``isinstance(exc, OSError)`` branch in
``_retry_on_error`` was silently retrying permanent 400 / 401 / 403
/ 404 failures five times with exponential backoff — wasting ~80
seconds per failed call across ~9 tabs per cycle.

These tests pin the 4xx-no-retry semantics for gsheets, mirroring
``tests/test_drive_apostrophe_and_retry.py`` for drive.
"""

from unittest.mock import MagicMock

import pytest


def _make_http_error(status_code):
    """Build a requests.HTTPError carrying the given response status."""
    import requests
    response = MagicMock()
    response.status_code = status_code
    err = requests.exceptions.HTTPError(
        f"{status_code} Client Error", response=response)
    return err


class TestRetryGuardOn4xx:
    """4xx (other than 429) is permanent — retrying wastes ~80s per
    failed call across all tabs in a sync cycle.  ``_retry_on_error``
    must distinguish 4xx (permanent) from 429 / 5xx / network
    (transient)."""

    @pytest.mark.parametrize('status', [400, 401, 403, 404])
    def test_4xx_does_not_retry(self, status, monkeypatch):
        from fam.sync import gsheets as gs_mod
        sleep_calls = []
        monkeypatch.setattr(gs_mod.time, 'sleep',
                            lambda s: sleep_calls.append(s))

        call_count = {'n': 0}

        def _always_4xx():
            call_count['n'] += 1
            raise _make_http_error(status)

        with pytest.raises(Exception):
            gs_mod._retry_on_error(_always_4xx)

        assert call_count['n'] == 1, (
            f"4xx ({status}) is permanent; _retry_on_error must call "
            f"the function exactly ONCE then re-raise.  Got "
            f"{call_count['n']} calls — retries on 4xx waste ~80s "
            f"per failed call across ~9 tabs per sync cycle and "
            f"produce misleading 'transient error' warnings.")
        assert sleep_calls == [], (
            "Sleep / back-off must not be invoked for 4xx errors")

    def test_429_still_retries(self, monkeypatch):
        """Rate-limit (429) IS transient; retry is correct."""
        from fam.sync import gsheets as gs_mod
        sleep_calls = []
        monkeypatch.setattr(gs_mod.time, 'sleep',
                            lambda s: sleep_calls.append(s))

        call_count = {'n': 0}

        def _always_429():
            call_count['n'] += 1
            raise _make_http_error(429)

        with pytest.raises(Exception):
            gs_mod._retry_on_error(_always_429)

        assert call_count['n'] >= 2, (
            "429 (rate-limit) IS transient and SHOULD be retried")
        assert len(sleep_calls) >= 1

    @pytest.mark.parametrize('status', [500, 502, 503])
    def test_5xx_still_retries(self, status, monkeypatch):
        from fam.sync import gsheets as gs_mod
        sleep_calls = []
        monkeypatch.setattr(gs_mod.time, 'sleep',
                            lambda s: sleep_calls.append(s))

        call_count = {'n': 0}

        def _always_5xx():
            call_count['n'] += 1
            raise _make_http_error(status)

        with pytest.raises(Exception):
            gs_mod._retry_on_error(_always_5xx)

        assert call_count['n'] >= 2

    def test_network_error_still_retries(self, monkeypatch):
        """ConnectionError / TimeoutError remain transient."""
        from fam.sync import gsheets as gs_mod
        sleep_calls = []
        monkeypatch.setattr(gs_mod.time, 'sleep',
                            lambda s: sleep_calls.append(s))
        call_count = {'n': 0}

        def _net_fail():
            call_count['n'] += 1
            raise ConnectionError("DNS failure")

        with pytest.raises(Exception):
            gs_mod._retry_on_error(_net_fail)

        assert call_count['n'] >= 2

    def test_timeout_still_retries(self, monkeypatch):
        from fam.sync import gsheets as gs_mod
        sleep_calls = []
        monkeypatch.setattr(gs_mod.time, 'sleep',
                            lambda s: sleep_calls.append(s))
        call_count = {'n': 0}

        def _timeout():
            call_count['n'] += 1
            raise TimeoutError("read timeout")

        with pytest.raises(Exception):
            gs_mod._retry_on_error(_timeout)

        assert call_count['n'] >= 2

    def test_success_on_first_try_no_retry(self):
        from fam.sync import gsheets as gs_mod

        def _ok():
            return 'value'

        assert gs_mod._retry_on_error(_ok) == 'value'

    def test_eventual_success_after_429(self, monkeypatch):
        """Transient failures recover."""
        from fam.sync import gsheets as gs_mod
        monkeypatch.setattr(gs_mod.time, 'sleep', lambda s: None)
        call_count = {'n': 0}

        def _flaky():
            call_count['n'] += 1
            if call_count['n'] < 3:
                raise _make_http_error(429)
            return 'value'

        assert gs_mod._retry_on_error(_flaky) == 'value'
        assert call_count['n'] == 3
