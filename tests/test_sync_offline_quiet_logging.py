"""Quiet offline-error logging
(v1.9.10 follow-up, 2026-05-01).

Background — the Bethel Park 2026-05-01 incident:
    The laptop lost internet for ~15 minutes.  The auto-sync timer
    fires every 5 minutes, and each tick syncs ~6 sheet tabs.
    Each tab tried to authorize against ``oauth2.googleapis.com``,
    each authorization failed with the same DNS-unresolved chain
    (socket.gaierror → urllib3.NameResolutionError →
    requests.ConnectionError → google.auth.TransportError), and
    the gsheets backend logged a full ``logger.exception`` for
    every tab.  Three sync ticks × six tabs = ~18 stack-trace
    blocks, hundreds of lines of log per outage, completely
    drowning out anything else.

These tests pin the fix:

  1. ``_is_offline_error`` correctly classifies the entire
     Bethel Park exception chain.
  2. Offline-class failures emit ONE concise WARN line per
     cycle — not a full traceback per tab.
  3. Real bugs (auth failures, schema errors, programming
     mistakes) STILL get the full traceback so we can debug
     them.  This is the regression guard against silencing
     real errors.
  4. ``SyncResult.offline=True`` is set so callers (UI status
     bar, sync history) can distinguish outage from real error.
  5. ``SyncManager.sync_all`` coalesces all-offline cycles
     into a single warning line.
"""

import logging
import socket
from unittest.mock import MagicMock, patch

import pytest

from fam.sync.base import SyncResult, SyncBackend
from fam.sync.gsheets import (
    _is_offline_error,
    _OFFLINE_EXC_NAMES,
    _WINSOCK_OFFLINE_ERRNOS,
    GoogleSheetsBackend,
)
from fam.sync.manager import SyncManager


# ════════════════════════════════════════════════════════════════════
# 1. _is_offline_error — classifier coverage
# ════════════════════════════════════════════════════════════════════


def _build_chain(*exc_classes_and_args):
    """Build a chained exception the same way Python does at runtime.

    ``[(cls1, kwargs1), (cls2, kwargs2), ...]`` produces
    ``cls1`` raised ``from cls2`` raised ``from cls3`` ... so the
    OUTER exception is at index 0, just like the user-visible
    traceback.
    """
    excs = []
    for cls, kwargs in exc_classes_and_args:
        try:
            excs.append(cls(**kwargs))
        except TypeError:
            # Some exception classes take positional args only.
            excs.append(cls(*kwargs.values()))
    # Wire them: outer.__cause__ = next, ... innermost.__cause__ = None
    for i in range(len(excs) - 1):
        excs[i].__cause__ = excs[i + 1]
    return excs[0]


class TestOfflineErrorClassifier:
    """``_is_offline_error`` must return True for every link in
    the Bethel Park chain and False for unrelated errors."""

    def test_socket_gaierror_detected(self):
        """The deepest cause: ``socket.gaierror`` from a DNS lookup."""
        exc = socket.gaierror(11001, "getaddrinfo failed")
        assert _is_offline_error(exc) is True

    def test_winsock_errno_11001(self):
        """Errno 11001 (WSAHOST_NOT_FOUND) — the exact Windows DNS
        failure code in the Bethel Park log."""
        exc = OSError(11001, "Host not found")
        assert _is_offline_error(exc) is True

    @pytest.mark.parametrize('errno_val', sorted(_WINSOCK_OFFLINE_ERRNOS))
    def test_all_known_winsock_errnos(self, errno_val):
        """Every WinSock errno in the offline set must classify
        as offline."""
        exc = OSError(errno_val, "network err")
        assert _is_offline_error(exc) is True

    def test_timeout_error_detected(self):
        """A bare socket timeout — common during flaky WiFi."""
        exc = TimeoutError("timed out")
        assert _is_offline_error(exc) is True

    def test_class_name_match_detects_urllib3_resolution_error(self):
        """We don't import urllib3 just to do this check — match
        by class name.  Simulate a NameResolutionError."""
        class NameResolutionError(Exception):
            pass
        exc = NameResolutionError("Failed to resolve")
        assert _is_offline_error(exc) is True

    def test_class_name_match_detects_google_transport_error(self):
        class TransportError(Exception):
            pass
        exc = TransportError("Failed to fetch token")
        assert _is_offline_error(exc) is True

    @pytest.mark.parametrize('cls_name', sorted(_OFFLINE_EXC_NAMES))
    def test_all_known_offline_exc_names(self, cls_name):
        """Every class name in the offline set must classify."""
        cls = type(cls_name, (Exception,), {})
        assert _is_offline_error(cls("x")) is True

    def test_chain_walked_to_innermost_cause(self):
        """The user-visible exception is layers above the gaierror.
        The classifier must walk through every link."""
        # Simulate: TransportError ← ConnectionError ← MaxRetryError
        # ← NameResolutionError ← gaierror   (Bethel Park exact chain)
        gai = socket.gaierror(11001, "getaddrinfo failed")
        name_res = type('NameResolutionError', (Exception,), {})(
            "Failed to resolve")
        name_res.__cause__ = gai
        max_retry = type('MaxRetryError', (Exception,), {})(
            "Max retries exceeded")
        max_retry.__cause__ = name_res
        conn_err = type('ConnectionError', (Exception,), {})(
            "ConnectionError")
        conn_err.__cause__ = max_retry
        transport = type('TransportError', (Exception,), {})(
            "Failed to fetch token")
        transport.__cause__ = conn_err

        # Outer-most exception classifies as offline
        assert _is_offline_error(transport) is True

    def test_chain_via_context_not_just_cause(self):
        """Implicit ``raise during except`` chains via __context__,
        not __cause__.  Must walk that path too."""
        gai = socket.gaierror(11001, "getaddrinfo failed")
        outer = RuntimeError("oops")
        outer.__context__ = gai  # implicit chain — no `from`
        assert _is_offline_error(outer) is True

    def test_circular_chain_does_not_loop_forever(self):
        """Defensive: a malformed chain (cycle) must not hang."""
        a = RuntimeError("a")
        b = RuntimeError("b")
        a.__cause__ = b
        b.__cause__ = a  # circular
        # Whatever the answer, the call must terminate quickly.
        result = _is_offline_error(a)
        assert isinstance(result, bool)

    # ── Negative tests: real bugs must NOT classify as offline ──

    def test_value_error_is_not_offline(self):
        """A coding mistake must keep its full traceback."""
        assert _is_offline_error(ValueError("bad data")) is False

    def test_key_error_is_not_offline(self):
        assert _is_offline_error(KeyError('column_x')) is False

    def test_type_error_is_not_offline(self):
        assert _is_offline_error(TypeError("expected str")) is False

    def test_random_oserror_is_not_offline(self):
        """OSError with a non-network errno (e.g. EACCES = permission
        denied) is NOT offline-class — it's a real bug."""
        exc = OSError(13, "Permission denied")
        assert _is_offline_error(exc) is False

    def test_auth_error_message_alone_is_not_offline(self):
        """A google-auth ``RefreshError`` is a bad-credentials bug,
        NOT a network outage.  Must keep its traceback."""
        class RefreshError(Exception):
            pass
        exc = RefreshError("invalid_grant: bad signature")
        assert _is_offline_error(exc) is False


# ════════════════════════════════════════════════════════════════════
# 2. upsert_rows logs ONE warning, not a full traceback
# ════════════════════════════════════════════════════════════════════


def _make_offline_chain():
    """Build a realistic offline exception chain (mimics Bethel Park)."""
    gai = socket.gaierror(11001, "getaddrinfo failed")
    transport = type('TransportError', (Exception,), {})(
        "Failed to fetch token")
    transport.__cause__ = gai
    return transport


class TestUpsertRowsQuietOffline:
    """``upsert_rows`` must log exactly ONE warning per cycle when
    the failure is offline-class — not a full traceback per tab."""

    def test_first_offline_emits_one_warning(self, caplog):
        backend = GoogleSheetsBackend()
        # Force the offline error during _authorize.
        with patch.object(
                backend, '_authorize',
                side_effect=_make_offline_chain()):
            with caplog.at_level(logging.WARNING, logger='fam.sync.gsheets'):
                result = backend.upsert_rows(
                    "Vendor Reimbursement", [{'a': 1}], ['a'])

        assert result.success is False
        assert result.offline is True
        assert result.error == "Network unavailable"

        # Exactly ONE warning record (not a traceback).
        warnings = [r for r in caplog.records
                    if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "network unavailable" in warnings[0].getMessage().lower()
        # Critical: no exc_info attached → no traceback rendered.
        assert warnings[0].exc_info is None

    def test_subsequent_offline_calls_silent_in_same_cycle(self, caplog):
        """The first offline failure logs.  The next 5 don't —
        they short-circuit silently."""
        backend = GoogleSheetsBackend()
        with patch.object(
                backend, '_authorize',
                side_effect=_make_offline_chain()):
            with caplog.at_level(logging.WARNING, logger='fam.sync.gsheets'):
                # First call — logs.
                r1 = backend.upsert_rows("Tab1", [{'a': 1}], ['a'])
                # Subsequent calls in the same cycle — silent.
                r2 = backend.upsert_rows("Tab2", [{'a': 1}], ['a'])
                r3 = backend.upsert_rows("Tab3", [{'a': 1}], ['a'])
                r4 = backend.upsert_rows("Tab4", [{'a': 1}], ['a'])
                r5 = backend.upsert_rows("Tab5", [{'a': 1}], ['a'])
                r6 = backend.upsert_rows("Tab6", [{'a': 1}], ['a'])

        # All 6 results are offline failures.
        for r in (r1, r2, r3, r4, r5, r6):
            assert r.offline is True
            assert r.success is False

        # But only ONE warning was logged.
        offline_warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and 'network unavailable' in r.getMessage().lower()
        ]
        assert len(offline_warnings) == 1, (
            f"expected 1 offline warning, got {len(offline_warnings)}: "
            f"{[w.getMessage() for w in offline_warnings]}")

    def test_reset_state_re_arms_logging(self, caplog):
        """After ``reset_offline_state``, the next offline event
        logs again (it's a NEW cycle)."""
        backend = GoogleSheetsBackend()
        with patch.object(
                backend, '_authorize',
                side_effect=_make_offline_chain()):
            with caplog.at_level(logging.WARNING, logger='fam.sync.gsheets'):
                backend.upsert_rows("Tab1", [{'a': 1}], ['a'])
                backend.upsert_rows("Tab2", [{'a': 1}], ['a'])  # silent

                # Simulate next sync cycle.
                backend.reset_offline_state()

                backend.upsert_rows("Tab3", [{'a': 1}], ['a'])  # logs again
                backend.upsert_rows("Tab4", [{'a': 1}], ['a'])  # silent

        offline_warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and 'network unavailable' in r.getMessage().lower()
        ]
        # ONE per cycle: cycle 1 + cycle 2 = 2 warnings total.
        assert len(offline_warnings) == 2

    # ── Real-bug regression: traceback MUST still appear ──

    def test_real_error_still_produces_traceback(self, caplog):
        """A non-network exception must keep its full traceback so
        the bug is visible in the log."""
        backend = GoogleSheetsBackend()
        with patch.object(
                backend, '_authorize',
                side_effect=ValueError("bad config — debug me!")):
            with caplog.at_level(logging.ERROR, logger='fam.sync.gsheets'):
                result = backend.upsert_rows(
                    "Vendor Reimbursement", [{'a': 1}], ['a'])

        assert result.success is False
        # NOT offline — this is a real error.
        assert result.offline is False
        # exc_info attached → traceback rendered to log.
        errors_with_tb = [
            r for r in caplog.records
            if r.levelno == logging.ERROR and r.exc_info is not None
        ]
        assert len(errors_with_tb) >= 1, (
            "real (non-network) errors must still log a full "
            "traceback so we can debug them — silencing them was "
            "the whole worry about this change")


# ════════════════════════════════════════════════════════════════════
# 3. Manager-level coalescing: one summary line per all-offline cycle
# ════════════════════════════════════════════════════════════════════


class _StubBackend(SyncBackend):
    """A SyncBackend that returns predetermined SyncResults — used
    to test the manager's logging behavior without going through the
    real gspread stack."""

    def __init__(self, per_tab_result):
        self._per_tab = per_tab_result
        self._reset_count = 0

    def is_configured(self):
        return True

    def validate_connection(self):
        return SyncResult(success=True)

    def upsert_rows(self, sheet_name, rows, key_columns, delete_stale=True):
        # Look up by sheet_name; default to a generic offline result.
        return self._per_tab.get(
            sheet_name,
            SyncResult(success=False, error="Network unavailable",
                       offline=True))

    def delete_rows(self, sheet_name, market_code, device_id):
        return SyncResult(success=True)

    def read_rows(self, sheet_name, market_code=None, device_id=None):
        return []

    def reset_offline_state(self):
        self._reset_count += 1


class TestSyncManagerCoalesces:
    """``SyncManager.sync_all`` must produce ONE summary log line
    per all-offline cycle, and must call ``reset_offline_state`` on
    the backend at the start of each cycle."""

    def _build_offline_data(self):
        """Six tabs of input — same shape as the real Bethel Park
        sync."""
        return {
            'Detailed Ledger': [{'market_code': 'BP', 'device_id': 'd1'}],
            'FMNP Entries': [{'market_code': 'BP', 'device_id': 'd1'}],
            'Geolocation': [{'market_code': 'BP', 'device_id': 'd1'}],
            'Vendor Reimbursement': [{'market_code': 'BP', 'device_id': 'd1'}],
            'Error Log': [{'market_code': 'BP', 'device_id': 'd1'}],
        }

    def test_all_offline_cycle_logs_one_summary(self, caplog):
        """Six tabs all fail offline → one WARN summary line, no
        per-tab "Sync complete" INFO line."""
        backend = _StubBackend(per_tab_result={})  # all default → offline
        manager = SyncManager(backend, throttle_writes=False)

        # Force the agent-tracker step to be a no-op so it doesn't
        # contaminate our log assertions.
        with patch.object(manager, '_sync_agent_tracker',
                          return_value=SyncResult(success=False,
                                                  offline=True)):
            with caplog.at_level(logging.INFO, logger='fam.sync.manager'):
                results = manager.sync_all(self._build_offline_data())

        # Every tab is a failure tagged offline.
        assert all(r.offline for r in results.values()
                   if not r.success)

        # ONE summary line — and it's a WARNING about network
        # unavailability, NOT the per-tab "Sync complete: X/Y" INFO.
        summary_warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and 'network unavailable' in r.getMessage().lower()
        ]
        assert len(summary_warnings) == 1

        sync_complete_lines = [
            r for r in caplog.records
            if 'sync complete' in r.getMessage().lower()
        ]
        assert sync_complete_lines == [], (
            "When the entire cycle is offline, the noisy "
            "'Sync complete: 0 rows, 6 failures' line should be "
            "suppressed in favor of the single summary warning")

    def test_some_success_with_only_offline_failures_still_summarizes(
            self, caplog):
        """Some tabs succeed, the rest fail offline → still emit
        the offline summary (not the noisy per-tab line).  The
        operator already knows the situation: the network is
        intermittent.  No need to dump a 'Sync complete: 2/5,
        3 failures' line on top.

        The key invariant: NO error-level traceback noise.
        """
        backend = _StubBackend(per_tab_result={
            'Detailed Ledger': SyncResult(success=True, rows_synced=10),
            'FMNP Entries': SyncResult(success=True, rows_synced=5),
            # Others default to offline.
        })
        manager = SyncManager(backend, throttle_writes=False)

        with patch.object(manager, '_sync_agent_tracker',
                          return_value=SyncResult(success=True)):
            with caplog.at_level(logging.INFO, logger='fam.sync.manager'):
                manager.sync_all(self._build_offline_data())

        # All failures are offline → coalesce to a single warning.
        offline_summaries = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and 'network unavailable' in r.getMessage().lower()
        ]
        assert len(offline_summaries) == 1
        # Critical noise-reduction invariant: NO error tracebacks.
        errors_with_tb = [
            r for r in caplog.records
            if r.levelno >= logging.ERROR and r.exc_info is not None
        ]
        assert errors_with_tb == []

    def test_full_success_keeps_normal_log_line(self, caplog):
        """Every tab succeeds → normal 'Sync complete' INFO line
        appears as before (no behavior change for the happy path)."""
        backend = _StubBackend(per_tab_result={
            'Detailed Ledger': SyncResult(success=True, rows_synced=10),
            'FMNP Entries': SyncResult(success=True, rows_synced=5),
            'Geolocation': SyncResult(success=True, rows_synced=3),
            'Vendor Reimbursement': SyncResult(success=True, rows_synced=8),
            'Error Log': SyncResult(success=True, rows_synced=1),
        })
        manager = SyncManager(backend, throttle_writes=False)

        with patch.object(manager, '_sync_agent_tracker',
                          return_value=SyncResult(success=True)):
            with caplog.at_level(logging.INFO, logger='fam.sync.manager'):
                manager.sync_all(self._build_offline_data())

        sync_complete_lines = [
            r for r in caplog.records
            if 'sync complete' in r.getMessage().lower()
        ]
        assert len(sync_complete_lines) == 1
        # And no offline summary fired.
        offline_summaries = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and 'network unavailable' in r.getMessage().lower()
        ]
        assert offline_summaries == []

    def test_real_error_keeps_normal_log_line(self, caplog):
        """A real (non-offline) failure must NOT trigger the
        offline-summary path — those still need to be visible."""
        backend = _StubBackend(per_tab_result={
            'Detailed Ledger': SyncResult(
                success=False, error="schema mismatch", offline=False),
            'FMNP Entries': SyncResult(
                success=False, error="quota exceeded", offline=False),
        })
        # Limit input to two tabs so we have a clean assertion target.
        manager = SyncManager(backend, throttle_writes=False)

        with patch.object(manager, '_sync_agent_tracker',
                          return_value=SyncResult(success=False)):
            with caplog.at_level(logging.INFO, logger='fam.sync.manager'):
                manager.sync_all({
                    'Detailed Ledger': [{}],
                    'FMNP Entries': [{}],
                })

        # No "network unavailable" summary fires.
        offline_summaries = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and 'network unavailable' in r.getMessage().lower()
        ]
        assert offline_summaries == []
        # Normal "Sync complete" line IS produced.
        sync_complete = [
            r for r in caplog.records
            if 'sync complete' in r.getMessage().lower()
        ]
        assert len(sync_complete) == 1

    def test_manager_resets_backend_offline_state(self):
        """Each ``sync_all`` call must invoke ``reset_offline_state``
        on the backend so a previous outage doesn't permanently mute
        the next cycle's logging."""
        backend = _StubBackend(per_tab_result={})
        manager = SyncManager(backend, throttle_writes=False)
        with patch.object(manager, '_sync_agent_tracker',
                          return_value=SyncResult(success=False)):
            manager.sync_all({'Detailed Ledger': [{}]})
            manager.sync_all({'Detailed Ledger': [{}]})
            manager.sync_all({'Detailed Ledger': [{}]})
        assert backend._reset_count == 3


# ════════════════════════════════════════════════════════════════════
# 4. SyncResult.offline flag round-trips
# ════════════════════════════════════════════════════════════════════


class TestSyncResultOfflineFlag:

    def test_default_false(self):
        r = SyncResult(success=True)
        assert r.offline is False

    def test_explicit_true(self):
        r = SyncResult(success=False, error="x", offline=True)
        assert r.offline is True

    def test_repr_includes_offline(self):
        r = SyncResult(success=False, offline=True, error="x")
        assert 'offline=True' in repr(r)


# ════════════════════════════════════════════════════════════════════
# 5. Bethel Park reproducer — the exact scenario from the log
# ════════════════════════════════════════════════════════════════════


class TestBethelParkReproducer:
    """End-to-end: feed the manager the EXACT exception chain that
    appeared in the Bethel Park 2026-05-01 log and verify the new
    behavior — ONE warning per cycle, not 6+ tracebacks."""

    def test_six_tabs_offline_produces_two_warnings_total(self, caplog):
        """One backend-level warning (first tab) + one
        manager-level summary = 2 total log records.
        (Was: 6 ERROR tracebacks + 1 misleading INFO before the fix.)"""
        backend = GoogleSheetsBackend()

        # Make _authorize fail with the exact Bethel Park chain
        # every time it's called (which happens once per tab in
        # the absence of caching).
        gai = socket.gaierror(11001, "getaddrinfo failed")
        # Simulate the urllib3 → google.auth chain.
        for cls_name in (
                'NameResolutionError', 'MaxRetryError',
                'ConnectionError', 'TransportError'):
            cls = type(cls_name, (Exception,), {})
            wrapped = cls(f"{cls_name}: simulated")
            wrapped.__cause__ = gai
            gai = wrapped

        manager = SyncManager(backend, throttle_writes=False)

        with patch.object(backend, '_authorize', side_effect=gai):
            with patch.object(manager, '_sync_agent_tracker',
                              return_value=SyncResult(success=False,
                                                      offline=True)):
                with caplog.at_level(logging.DEBUG):
                    manager.sync_all({
                        'Detailed Ledger': [{}],
                        'FMNP Entries': [{}],
                        'Geolocation': [{}],
                        'Vendor Reimbursement': [{}],
                        'Error Log': [{}],
                        'Agent Tracker': [{}],
                    })

        # ZERO error-level records with tracebacks (was 6+).
        errors_with_tb = [
            r for r in caplog.records
            if r.levelno >= logging.ERROR and r.exc_info is not None
        ]
        assert errors_with_tb == [], (
            f"Bethel Park reproducer: expected 0 ERROR tracebacks, "
            f"got {len(errors_with_tb)}.  This is the noisy "
            f"behavior the fix is supposed to eliminate.")

        # Exactly the expected concise warnings:
        #   - one from gsheets backend (first tab to fail)
        #   - one from manager (cycle summary)
        offline_warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and 'network unavailable' in r.getMessage().lower()
        ]
        assert len(offline_warnings) == 2, (
            f"expected 2 offline warnings (1 backend + 1 manager "
            f"summary), got {len(offline_warnings)}: "
            f"{[w.getMessage() for w in offline_warnings]}")
