"""Regression tests for the v1.9.7 Drive verification fix.

Before v1.9.7, ``_verify_file_in_drive`` returned a boolean where False
conflated "confirmed missing" with "couldn't verify right now" — a
transient DNS hiccup during verification caused the caller to clear
the Drive URL from the database, producing a re-upload storm on the
next sync.

This was particularly dangerous for heavy-FMNP markets where hundreds
of photo URLs are verified per sync cycle; a single network flap could
trigger mass re-uploads, wasting bandwidth and Drive API quota.

The v1.9.7 fix introduces:

  * ``VerifyResult`` tri-state (EXISTS / TRASHED_OR_MISSING / UNKNOWN)
  * ``_verify_and_clear_dead_urls`` only clears on TRASHED_OR_MISSING
  * ``_verification_throttled`` limits the sweep to at most once per
    10 minutes regardless of how many syncs fire in between
  * Network errors log single-line WARN instead of full tracebacks
"""

import time
from unittest.mock import patch, MagicMock

import pytest

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.sync.drive import (
    VerifyResult,
    _verify_file_in_drive,
    _verify_and_clear_dead_urls,
    _verification_throttled,
    _mark_verification_complete,
    _VERIFICATION_MIN_INTERVAL_SEC,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_drive_verification.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield get_connection()
    close_connection()


# ══════════════════════════════════════════════════════════════════
# _verify_file_in_drive — tri-state return values
# ══════════════════════════════════════════════════════════════════
class TestVerifyFileTriState:
    """Every code path in _verify_file_in_drive must map to exactly one
    of EXISTS / TRASHED_OR_MISSING / UNKNOWN — never the old boolean."""

    def _mock_response(self, status_code, json_body=None, text=""):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_body or {}
        resp.text = text
        return resp

    def test_200_not_trashed_returns_exists(self):
        session = MagicMock()
        session.get.return_value = self._mock_response(
            200, {'id': 'abc', 'name': 'f.jpg', 'size': '1024', 'trashed': False})
        assert _verify_file_in_drive(session, 'abc') == VerifyResult.EXISTS

    def test_200_trashed_returns_missing(self):
        session = MagicMock()
        session.get.return_value = self._mock_response(
            200, {'id': 'abc', 'name': 'f.jpg', 'size': '1024', 'trashed': True})
        assert _verify_file_in_drive(session, 'abc') == VerifyResult.TRASHED_OR_MISSING

    def test_404_returns_missing(self):
        """A confirmed 404 is definitive — the file is not there."""
        session = MagicMock()
        session.get.return_value = self._mock_response(404, text="Not Found")
        assert _verify_file_in_drive(session, 'abc') == VerifyResult.TRASHED_OR_MISSING

    def test_401_returns_unknown_not_missing(self):
        """Auth failure must NOT be treated as missing — silently
        clearing URLs because the token expired would be catastrophic."""
        session = MagicMock()
        session.get.return_value = self._mock_response(401, text="Unauthorized")
        assert _verify_file_in_drive(session, 'abc') == VerifyResult.UNKNOWN

    def test_403_returns_unknown(self):
        session = MagicMock()
        session.get.return_value = self._mock_response(403, text="Forbidden")
        assert _verify_file_in_drive(session, 'abc') == VerifyResult.UNKNOWN

    def test_429_returns_unknown(self):
        """Rate-limited — retry next cycle, don't clear."""
        session = MagicMock()
        session.get.return_value = self._mock_response(429, text="Rate limited")
        assert _verify_file_in_drive(session, 'abc') == VerifyResult.UNKNOWN

    def test_500_returns_unknown(self):
        """Drive server error — retry next cycle, don't clear."""
        session = MagicMock()
        session.get.return_value = self._mock_response(500, text="oops")
        assert _verify_file_in_drive(session, 'abc') == VerifyResult.UNKNOWN

    def test_503_returns_unknown(self):
        session = MagicMock()
        session.get.return_value = self._mock_response(503)
        assert _verify_file_in_drive(session, 'abc') == VerifyResult.UNKNOWN

    def test_unexpected_status_returns_unknown(self):
        """Conservative: any other status code is UNKNOWN, not missing."""
        session = MagicMock()
        session.get.return_value = self._mock_response(418, text="Teapot")
        assert _verify_file_in_drive(session, 'abc') == VerifyResult.UNKNOWN


class TestVerifyFileNetworkErrorHandling:
    """Network errors (DNS failures, timeouts, connection resets) must
    return UNKNOWN and log a single-line WARN — not the old behavior of
    returning False (treated as missing) + full ERROR traceback."""

    def test_connection_error_returns_unknown(self):
        import requests.exceptions as rexc
        session = MagicMock()
        session.get.side_effect = rexc.ConnectionError("DNS failed")
        assert _verify_file_in_drive(session, 'abc') == VerifyResult.UNKNOWN

    def test_timeout_returns_unknown(self):
        import requests.exceptions as rexc
        session = MagicMock()
        session.get.side_effect = rexc.Timeout("too slow")
        assert _verify_file_in_drive(session, 'abc') == VerifyResult.UNKNOWN

    def test_transport_error_returns_unknown(self):
        """google-auth's TransportError wraps connection failures during
        the OAuth token refresh step — exactly what the user observed
        in v1.9.6 production logs."""
        import google.auth.exceptions as gauth
        session = MagicMock()
        session.get.side_effect = gauth.TransportError("oauth2 unreachable")
        assert _verify_file_in_drive(session, 'abc') == VerifyResult.UNKNOWN

    def test_network_error_logs_single_line_warning(self, caplog):
        """The old code did logger.exception which dumps the full
        traceback at ERROR level.  The fix should log a single WARN
        line with just the exception class name — much quieter."""
        import logging
        import requests.exceptions as rexc
        session = MagicMock()
        session.get.side_effect = rexc.ConnectionError("DNS failed")

        with caplog.at_level(logging.WARNING, logger='fam.sync.drive'):
            _verify_file_in_drive(session, 'abc')

        # At least one WARN-level message about skipping
        skip_msgs = [r for r in caplog.records
                     if r.levelno == logging.WARNING and 'skipped' in r.message]
        assert skip_msgs, "Expected a single WARNING about skipped verification"
        # And the old ERROR-level traceback must NOT appear
        error_msgs = [r for r in caplog.records
                      if r.levelno == logging.ERROR]
        assert not error_msgs, \
            "Network errors must not be logged at ERROR (too noisy)"

    def test_unexpected_exception_still_returns_unknown(self):
        """Even if we hit some exotic exception class we didn't
        anticipate, the contract must hold: never return something that
        could be interpreted as TRASHED_OR_MISSING."""
        session = MagicMock()
        session.get.side_effect = RuntimeError("something weird")
        assert _verify_file_in_drive(session, 'abc') == VerifyResult.UNKNOWN


# ══════════════════════════════════════════════════════════════════
# _verify_and_clear_dead_urls — URL preservation on UNKNOWN
# ══════════════════════════════════════════════════════════════════
class TestClearDeadUrlsPreservesOnUnknown:
    """The critical regression test: UNKNOWN must not clear URLs from
    the database.  This is the 'Drive re-upload storm' fix."""

    def _seed_fmnp_with_url(self, conn, url='https://drive.google.com/file/d/abc/view'):
        conn.execute("INSERT INTO markets (id, name, address) VALUES (1, 'M', '1')")
        conn.execute("INSERT INTO market_days (id, market_id, date, status) "
                     "VALUES (1, 1, '2026-04-24', 'Open')")
        conn.execute("INSERT INTO vendors (id, name, is_active) VALUES (1, 'V', 1)")
        conn.execute(
            "INSERT INTO fmnp_entries (id, market_day_id, vendor_id, amount, "
            "entered_by, photo_drive_url) "
            "VALUES (1, 1, 1, 500, 'Admin', ?)", (url,))
        conn.commit()
        return url

    def _bypass_throttle(self, fresh_db):
        """Make sure the throttle doesn't mask test behavior."""
        fresh_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES "
            "('drive_verification_last_run', '0')"
        )
        fresh_db.commit()

    def test_unknown_result_preserves_url_in_db(self, fresh_db):
        """CORE BUG FIX: a network-error UNKNOWN result during
        verification MUST leave the existing photo_drive_url intact.
        v1.9.6 cleared it, causing a re-upload storm on next sync."""
        self._bypass_throttle(fresh_db)
        original_url = self._seed_fmnp_with_url(fresh_db)

        with patch('fam.sync.drive._verify_file_in_drive',
                   return_value=VerifyResult.UNKNOWN):
            cleared = _verify_and_clear_dead_urls(MagicMock())

        # No URLs should be cleared under UNKNOWN
        assert cleared == 0

        # The DB row still has the original URL — not cleared, not changed
        row = fresh_db.execute(
            "SELECT photo_drive_url FROM fmnp_entries WHERE id=1").fetchone()
        assert row['photo_drive_url'] == original_url, \
            "UNKNOWN result must not clear the URL — that was the v1.9.6 bug"

    def test_trashed_or_missing_result_clears_url(self, fresh_db):
        """The legitimate case: Drive confirmed the file is gone."""
        self._bypass_throttle(fresh_db)
        self._seed_fmnp_with_url(fresh_db)

        with patch('fam.sync.drive._verify_file_in_drive',
                   return_value=VerifyResult.TRASHED_OR_MISSING):
            cleared = _verify_and_clear_dead_urls(MagicMock())

        assert cleared == 1

        row = fresh_db.execute(
            "SELECT photo_drive_url FROM fmnp_entries WHERE id=1").fetchone()
        assert row['photo_drive_url'] is None, \
            "TRASHED_OR_MISSING must clear the URL so re-upload is queued"

    def test_exists_result_keeps_url_no_clear(self, fresh_db):
        self._bypass_throttle(fresh_db)
        original_url = self._seed_fmnp_with_url(fresh_db)

        with patch('fam.sync.drive._verify_file_in_drive',
                   return_value=VerifyResult.EXISTS):
            cleared = _verify_and_clear_dead_urls(MagicMock())

        assert cleared == 0
        row = fresh_db.execute(
            "SELECT photo_drive_url FROM fmnp_entries WHERE id=1").fetchone()
        assert row['photo_drive_url'] == original_url

    def test_mixed_results_only_clears_confirmed_missing(self, fresh_db):
        """When a single entry has multiple URLs and verification returns
        a mix of EXISTS/UNKNOWN/TRASHED_OR_MISSING, only the confirmed-
        missing URLs get dropped.  UNKNOWN ones stay alongside EXISTS."""
        self._bypass_throttle(fresh_db)
        url1 = 'https://drive.google.com/file/d/aaa/view'
        url2 = 'https://drive.google.com/file/d/bbb/view'
        url3 = 'https://drive.google.com/file/d/ccc/view'
        # Store as JSON array (photo_paths helper format)
        combined = '["{0}", "{1}", "{2}"]'.format(url1, url2, url3)
        fresh_db.execute("INSERT INTO markets (id, name, address) VALUES (1, 'M', '1')")
        fresh_db.execute("INSERT INTO market_days (id, market_id, date, status) "
                         "VALUES (1, 1, '2026-04-24', 'Open')")
        fresh_db.execute("INSERT INTO vendors (id, name, is_active) VALUES (1, 'V', 1)")
        fresh_db.execute(
            "INSERT INTO fmnp_entries (id, market_day_id, vendor_id, amount, "
            "entered_by, photo_drive_url) VALUES (1, 1, 1, 500, 'Admin', ?)",
            (combined,))
        fresh_db.commit()

        # aaa EXISTS, bbb UNKNOWN, ccc TRASHED_OR_MISSING
        def fake_verify(session, file_id):
            return {
                'aaa': VerifyResult.EXISTS,
                'bbb': VerifyResult.UNKNOWN,
                'ccc': VerifyResult.TRASHED_OR_MISSING,
            }[file_id]

        with patch('fam.sync.drive._verify_file_in_drive', side_effect=fake_verify):
            cleared = _verify_and_clear_dead_urls(MagicMock())

        assert cleared == 1, "Only ccc (TRASHED_OR_MISSING) should clear"

        # The remaining URL list should contain url1 and url2 but NOT url3
        row = fresh_db.execute(
            "SELECT photo_drive_url FROM fmnp_entries WHERE id=1").fetchone()
        remaining = row['photo_drive_url'] or ''
        assert 'aaa' in remaining, "EXISTS url must remain"
        assert 'bbb' in remaining, "UNKNOWN url must remain (the bug fix)"
        assert 'ccc' not in remaining, "TRASHED url must be cleared"


# ══════════════════════════════════════════════════════════════════
# Verification throttle — 10-minute minimum interval
# ══════════════════════════════════════════════════════════════════
class TestVerificationThrottle:
    """Drive verification is expensive at heavy-FMNP scale (200+ URLs
    per cycle).  The throttle keeps it at most once per 10 minutes so
    a busy market with minute-cadence syncs doesn't burn Drive API
    quota on repeated verification of rarely-changing files."""

    def test_fresh_db_not_throttled(self, fresh_db):
        """With no prior run recorded, throttle must NOT block."""
        assert _verification_throttled() is False

    def test_just_ran_is_throttled(self, fresh_db):
        """Immediately after marking complete, the next call is throttled."""
        _mark_verification_complete()
        assert _verification_throttled() is True

    def test_old_timestamp_not_throttled(self, fresh_db):
        """If the recorded run was older than the interval, allow a
        fresh verification sweep."""
        past = _time_minus(seconds=_VERIFICATION_MIN_INTERVAL_SEC + 60)
        fresh_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES "
            "('drive_verification_last_run', ?)", (str(past),))
        fresh_db.commit()
        assert _verification_throttled() is False

    def test_corrupt_timestamp_treated_as_missing(self, fresh_db):
        """A garbage value in the settings table must not permanently
        block verification — fall back to allowing the sweep."""
        fresh_db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES "
            "('drive_verification_last_run', 'not-a-number')")
        fresh_db.commit()
        assert _verification_throttled() is False

    def test_throttled_clear_returns_zero(self, fresh_db):
        """When throttled, _verify_and_clear_dead_urls must return
        immediately with 0 cleared — no DB reads, no API calls."""
        _mark_verification_complete()
        with patch('fam.sync.drive._verify_file_in_drive') as mock_verify:
            cleared = _verify_and_clear_dead_urls(MagicMock())
        assert cleared == 0
        assert not mock_verify.called, \
            "Throttled run must not even touch the verification API"

    def test_successful_run_updates_timestamp(self, fresh_db):
        """After a full (non-throttled) run completes, the timestamp is
        recorded so subsequent syncs get throttled."""
        with patch('fam.sync.drive._verify_file_in_drive',
                   return_value=VerifyResult.EXISTS):
            _verify_and_clear_dead_urls(MagicMock())

        row = fresh_db.execute(
            "SELECT value FROM app_settings WHERE key = 'drive_verification_last_run'"
        ).fetchone()
        assert row is not None, "Successful run must record the timestamp"
        assert float(row['value']) > 0


def _time_minus(seconds):
    """Helper — wall-clock timestamp N seconds in the past."""
    return time.time() - seconds
