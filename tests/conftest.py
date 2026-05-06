"""Shared pytest fixtures.

Stable "today" for stale-market-day guards
==========================================
v1.9.9 added a guard rail that auto-closes any market day left
``Open`` with a date *before* ``eastern_today()``, plus a hard
guard in ``create_transaction`` that refuses to write to a market
day whose date is in the past.

Existing test fixtures hard-code historical dates like
``'2026-04-01'`` for the market day, then call
``create_transaction`` against it.  The guard interprets that as a
stale day relative to *real* today (whatever the system clock
says when the suite runs) and either auto-closes the day or
rejects the insert.

Tests aren't trying to exercise the stale-day flow — they're
exercising downstream behaviour given the fixture's hardcoded
"today."  We pin ``eastern_today`` to a date that's earlier than
every fixture date in the suite so the guard never spuriously
fires.  Tests that *do* want to exercise the stale-day flow
override this fixture inside their own scope.

The patch goes on the source module (``fam.utils.timezone``);
both production guards do an inside-function ``from
fam.utils.timezone import eastern_today`` so the lookup happens
at call time and picks up the patched value.
"""

from datetime import date

import pytest


# Earliest date used by any test fixture in the suite.  Production
# code never sees this — it's purely a test-time pin so the new
# v1.9.9 stale-day guards don't false-positive against fixtures
# that pre-date the guard.
_STABLE_TEST_TODAY = date(2026, 1, 1)


@pytest.fixture(autouse=True)
def _stable_eastern_today(monkeypatch):
    """Pin ``eastern_today`` for the test session so historical
    fixture dates don't trip the v1.9.9 stale-market-day guards."""
    from fam.utils import timezone
    monkeypatch.setattr(
        timezone, 'eastern_today',
        lambda: _STABLE_TEST_TODAY,
    )
    yield


# ── Auto-accept the v1.9.9 PaymentConfirmationDialog ─────────────
#
# The v1.9.9 confirm-payment redesign replaced the old
# ``QMessageBox.question`` plain-text prompt with a structured
# ``PaymentConfirmationDialog`` that has marching-ants animation,
# per-method action rows, and required checkboxes for external-
# device methods (SNAP/EBT).  Most tests stub
# ``QMessageBox.question`` to auto-Yes — but the new dialog is a
# ``QDialog.exec()``, so that stub no longer covers it.  Without
# this fixture, any test that ends up calling
# ``PaymentScreen._confirm_payment`` opens the dialog modally and
# hangs waiting for human interaction.
#
# Tests that explicitly want to drive the real dialog (e.g.
# ``tests/test_payment_confirmation_dialog.py``) instantiate it
# directly — they don't go through ``_confirm_payment`` — so this
# autouse stub doesn't affect them.
@pytest.fixture(autouse=True)
def _stub_qmessagebox(monkeypatch):
    """Stub Qt modal dialogs so tests don't fire real popup windows.

    Several Settings handlers and other UI code paths call
    ``QMessageBox.warning`` / ``.critical`` / ``.information`` /
    ``.question`` to surface errors and confirmations.  Without a
    stub, tests that hit those code paths spawn real modal windows
    that block the test runner until the operator manually clicks
    them — the runner appears to "hang" with multiple stacked
    dialogs.  Reported by the user during the v2.0.6 test sweep.

    The stub returns sensible defaults:
      * ``warning`` / ``critical`` / ``information`` → ``Ok``
      * ``question`` → ``Yes`` (most existing tests already
        monkeypatch this themselves; the auto-stub is a safety net)
    Tests that need to verify a specific dialog was shown can still
    monkeypatch over the stub at the test scope.
    """
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, 'warning',
        staticmethod(lambda *a, **kw: QMessageBox.Ok))
    monkeypatch.setattr(
        QMessageBox, 'critical',
        staticmethod(lambda *a, **kw: QMessageBox.Ok))
    monkeypatch.setattr(
        QMessageBox, 'information',
        staticmethod(lambda *a, **kw: QMessageBox.Ok))
    monkeypatch.setattr(
        QMessageBox, 'question',
        staticmethod(lambda *a, **kw: QMessageBox.Yes))
    yield


@pytest.fixture(autouse=True)
def _auto_accept_payment_confirmation(monkeypatch):
    """Stub ``PaymentConfirmationDialog.exec`` to return Accepted
    so tests that drive the full ``_confirm_payment`` flow don't
    hang on the modal dialog.  Ticks every required checkbox first
    so the contract (Confirm enabled only when all EBT-acks are
    ticked) still holds inside the test environment."""
    from PySide6.QtWidgets import QDialog
    from fam.ui.widgets.payment_confirmation_dialog import (
        PaymentConfirmationDialog,
    )

    def _auto_accept(self):
        # Tick every required checkbox (EBT acknowledgements) so
        # the Confirm button enables.  Without this the button is
        # disabled and ``accept()`` would not satisfy the dialog's
        # contract — the auto-stub mimics what a volunteer who
        # actually processed the SNAP swipe would do.
        for cb in self._required_checkboxes:
            cb.setChecked(True)
        # Stop the marching-ants timer so the test doesn't leak a
        # background QTimer into pytest-qt's event loop.
        for f in self._marching_ants_frames:
            f.stopAnimation()
        return QDialog.Accepted

    monkeypatch.setattr(
        PaymentConfirmationDialog, 'exec', _auto_accept)
    yield
