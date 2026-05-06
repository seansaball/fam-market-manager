"""Drive folder names with apostrophes + retry-on-4xx guard
(v2.0.1 fix, 2026-05-05).

User-reported regression: photo upload to Drive failed with
``400 Bad Request`` for any market or vendor folder name that
contained a literal apostrophe (e.g. "Sean's Test Market").  Two
underlying bugs converged:

  1. ``_get_or_create_subfolder`` (and ``_get_or_create_folder``)
     interpolated the folder name directly into the Drive ``q=``
     parameter inside a single-quoted string literal.  An
     apostrophe in the name terminated the literal early and
     produced a malformed query — Drive returned 400.

  2. ``_drive_retry`` excluded 4xx from its explicit retryable
     status list, BUT ``requests.HTTPError`` inherits from
     ``OSError`` (via ``IOError`` aliasing).  The
     ``isinstance(exc, OSError)`` branch silently retried
     permanent 400 / 401 / 403 / 404 failures three times,
     wasting ~5–10 seconds AND producing a misleading "transient
     error" log line.

These tests pin the fixes for both.
"""

from unittest.mock import MagicMock

import pytest


# ════════════════════════════════════════════════════════════════════
# 1. _escape_drive_query_string — the escape helper
# ════════════════════════════════════════════════════════════════════


class TestEscapeDriveQueryString:

    def test_plain_string_unchanged(self):
        from fam.sync.drive import _escape_drive_query_string
        assert _escape_drive_query_string("Bethel Park") == "Bethel Park"

    def test_apostrophe_escaped(self):
        from fam.sync.drive import _escape_drive_query_string
        # The literal user-reported case.
        assert (_escape_drive_query_string("Sean's Test Market")
                == "Sean\\'s Test Market")

    def test_multiple_apostrophes_all_escaped(self):
        from fam.sync.drive import _escape_drive_query_string
        assert (_escape_drive_query_string("O'Brien's Farm")
                == "O\\'Brien\\'s Farm")

    def test_backslash_escaped(self):
        from fam.sync.drive import _escape_drive_query_string
        assert _escape_drive_query_string("a\\b") == "a\\\\b"

    def test_backslash_then_apostrophe_order_correct(self):
        """Order matters: escape ``\\`` first, THEN ``'``.  Reversing
        would over-escape: a literal ``\\`` in input would become
        ``\\\\`` after the backslash pass, then if the apostrophe
        pass ran first, an apostrophe escaped as ``\\'`` would be
        re-escaped to ``\\\\'`` which is wrong."""
        from fam.sync.drive import _escape_drive_query_string
        # Input: backslash then apostrophe — common shell-style
        # mis-typed name.  Expected: ``\\\\`` then ``\\'``.
        assert (_escape_drive_query_string("a\\'b")
                == "a\\\\\\'b")

    def test_empty_string(self):
        from fam.sync.drive import _escape_drive_query_string
        assert _escape_drive_query_string("") == ""


# ════════════════════════════════════════════════════════════════════
# 2. Drive query construction uses escaped values
# ════════════════════════════════════════════════════════════════════


class TestQueryConstruction:
    """Mock the Drive session and verify the exact ``q=`` value
    sent to the Drive API includes the escaped folder name.  This
    is the only way to guard against future regressions reverting
    the escape (the network call would silently break again on
    the next deploy with a customer name containing ``'``)."""

    def _capture_query_for_subfolder(self, folder_name: str,
                                      parent_id: str = 'parent123'):
        from fam.sync.drive import _get_or_create_subfolder
        captured = {'q': None}

        def _fake_get(*args, **kwargs):
            captured['q'] = kwargs.get('params', {}).get('q')
            r = MagicMock()
            r.raise_for_status.return_value = None
            r.json.return_value = {'files': [{'id': 'cached_id'}]}
            return r

        session = MagicMock()
        session.get.side_effect = _fake_get
        _get_or_create_subfolder(session, parent_id, folder_name)
        return captured['q']

    def test_subfolder_search_with_apostrophe_escapes_correctly(self):
        q = self._capture_query_for_subfolder("Sean's Test Market")
        # The exact Bethel-Park-class breakage: an unescaped
        # apostrophe terminated the literal early.
        assert q is not None
        assert "name = 'Sean\\'s Test Market'" in q, (
            f"Drive query must include the escaped form "
            f"(Sean\\'s Test Market).  Got: {q!r}")
        # Belt-and-suspenders: the raw apostrophe-without-escape
        # pattern must NOT appear (it would be the bug signature).
        assert "name = 'Sean's" not in q

    def test_subfolder_search_no_apostrophe_unchanged(self):
        q = self._capture_query_for_subfolder("Bethel Park")
        assert q is not None
        assert "name = 'Bethel Park'" in q

    def test_top_level_folder_search_escapes_apostrophe(self):
        from fam.sync.drive import _get_or_create_folder
        captured = {'q': None}

        def _fake_get(*args, **kwargs):
            captured['q'] = kwargs.get('params', {}).get('q')
            r = MagicMock()
            r.raise_for_status.return_value = None
            r.json.return_value = {'files': [{'id': 'cached_id'}]}
            return r

        session = MagicMock()
        session.get.side_effect = _fake_get
        _get_or_create_folder(session, "Tom's Photos")
        assert "name = 'Tom\\'s Photos'" in captured['q']


# ════════════════════════════════════════════════════════════════════
# 3. _drive_retry refuses to retry on permanent 4xx
# ════════════════════════════════════════════════════════════════════


def _make_http_error(status_code):
    """Build a requests.HTTPError carrying the given response status.
    Used to simulate a Drive API client error."""
    import requests
    response = MagicMock()
    response.status_code = status_code
    err = requests.exceptions.HTTPError(
        f"{status_code} Client Error", response=response)
    return err


class TestRetryGuardOn4xx:
    """The user-reported case: 400 Bad Request for an apostrophe in
    folder name was retried 3 times by ``_drive_retry`` instead of
    failing fast.  The retry logic must distinguish 4xx (permanent)
    from 429 / 5xx / network (transient)."""

    @pytest.mark.parametrize('status', [400, 401, 403, 404])
    def test_4xx_does_not_retry(self, status, monkeypatch):
        from fam.sync import drive as drive_mod
        # Spy on time.sleep to ensure we don't sit in the back-off.
        sleep_calls = []
        monkeypatch.setattr(
            drive_mod, '_time',
            type('T', (), {'sleep': lambda s: sleep_calls.append(s)}),
        )

        call_count = {'n': 0}

        def _always_4xx():
            call_count['n'] += 1
            raise _make_http_error(status)

        with pytest.raises(Exception):
            drive_mod._drive_retry(_always_4xx, label='Test')

        assert call_count['n'] == 1, (
            f"4xx ({status}) is permanent; _drive_retry must call "
            f"the function exactly ONCE then re-raise.  Got "
            f"{call_count['n']} calls — retries on 4xx are wasted "
            f"work and produce misleading 'transient error' warnings.")
        assert sleep_calls == [], (
            "Sleep / back-off must not be invoked for 4xx errors")

    def test_429_still_retries(self, monkeypatch):
        """Rate-limit (429) IS transient; retry is correct."""
        from fam.sync import drive as drive_mod
        sleep_calls = []
        monkeypatch.setattr(
            drive_mod, '_time',
            type('T', (), {'sleep': lambda s: sleep_calls.append(s)}),
        )

        call_count = {'n': 0}

        def _always_429():
            call_count['n'] += 1
            raise _make_http_error(429)

        with pytest.raises(Exception):
            drive_mod._drive_retry(_always_429, label='Test')

        assert call_count['n'] >= 2, (
            "429 (rate-limit) IS transient and SHOULD be retried")
        assert len(sleep_calls) >= 1

    @pytest.mark.parametrize('status', [500, 502, 503])
    def test_5xx_still_retries(self, status, monkeypatch):
        from fam.sync import drive as drive_mod
        sleep_calls = []
        monkeypatch.setattr(
            drive_mod, '_time',
            type('T', (), {'sleep': lambda s: sleep_calls.append(s)}),
        )

        call_count = {'n': 0}

        def _always_5xx():
            call_count['n'] += 1
            raise _make_http_error(status)

        with pytest.raises(Exception):
            drive_mod._drive_retry(_always_5xx, label='Test')

        assert call_count['n'] >= 2

    def test_network_error_still_retries(self, monkeypatch):
        """ConnectionError / TimeoutError remain transient."""
        from fam.sync import drive as drive_mod
        sleep_calls = []
        monkeypatch.setattr(
            drive_mod, '_time',
            type('T', (), {'sleep': lambda s: sleep_calls.append(s)}),
        )
        call_count = {'n': 0}

        def _net_fail():
            call_count['n'] += 1
            raise ConnectionError("DNS failure")

        with pytest.raises(Exception):
            drive_mod._drive_retry(_net_fail, label='Test')

        assert call_count['n'] >= 2

    def test_success_on_first_try_no_retry(self):
        from fam.sync import drive as drive_mod

        def _ok():
            return 'value'

        assert drive_mod._drive_retry(_ok, label='Test') == 'value'
