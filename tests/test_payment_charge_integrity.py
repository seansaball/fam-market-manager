"""Charge-integrity guards on the Payment screen.

Background — the bug
====================
On the 2026-04 onsite market simulation a SNAP payment row was observed
with the spinbox showing $150 while the rest of the UI (summary cards,
collect panel, confirm dialog) and the saved DB row reported $200.  The
volunteer would naturally read $150 from the input field, collect that
much cash, and unknowingly undercharge the customer by $50.

Root cause: when the daily match cap reduces the FAM match below the
uncapped formula, ``calculate_payment_breakdown`` *inflates*
``customer_charged`` to keep ``method_amount`` equal to the receipt
total — but ``_update_summary`` only refreshed the row's match/total
LABELS via ``set_display_values`` and never wrote the inflated charge
back to the input spinbox.  Saving a draft persisted the inflated
amount; restoring derived the spinbox from ``method_amount/divisor``
which produced the under-cap value again, recreating the drift on
every reload.

These tests guard three independent layers of the fix so the bug
class is structurally impossible to ship to the database:

* **Layer 1** — ``_update_summary`` writes the engine's
  ``customer_charged`` back to the row's spinbox whenever they drift.
* **Layer 2** — ``_confirm_payment`` refuses to commit when any row's
  ``_get_active_charge()`` disagrees with the engine's
  ``customer_charged``, regardless of how the drift came to exist.
* **Layer 3** — ``PaymentRow.set_data`` accepts an explicit
  ``customer_charged`` so draft restore preserves the cap-inflated
  value instead of recomputing it from ``method_amount``.
"""

import pytest
from PySide6.QtCore import Qt

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.utils.money import dollars_to_cents, cents_to_dollars


# ──────────────────────────────────────────────────────────────────
# Fixture: market with a daily match limit, single SNAP-100% method
# ──────────────────────────────────────────────────────────────────
@pytest.fixture
def capped_db(tmp_path):
    """Fresh DB matching the screenshot scenario:
    - Test Market with match cap ON, $100/customer
    - SNAP (100%), Cash (0%), FMNP (100%, $5 denom)
    - One open market day
    """
    db_file = str(tmp_path / "test_charge_integrity.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()

    conn.execute(
        "INSERT INTO markets (id, name, address, daily_match_limit,"
        " match_limit_active) VALUES"
        " (1, 'Test Market', '123 Test Lane', 10000, 1)")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'Haffey Family Farm')")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (1, 'SNAP', 100.0, 1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active, sort_order)"
        " VALUES (2, 'Cash', 0.0, 1, 2)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active,"
        " sort_order, denomination) VALUES (3, 'FMNP', 100.0, 1, 3, 500)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, payment_method_id)"
        " VALUES (1, 1), (1, 2), (1, 3)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, opened_by)"
        " VALUES (1, 1, '2026-04-28', 'Open', 'Tester')")
    conn.commit()
    yield conn
    close_connection()


def _make_order(conn, receipt_total_cents: int, vendor_id: int = 1):
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import create_transaction
    order_id, _ = create_customer_order(market_day_id=1)
    create_transaction(
        market_day_id=1,
        vendor_id=vendor_id,
        receipt_total=receipt_total_cents,
        market_day_date='2026-04-28',
        customer_order_id=order_id,
    )
    return order_id


def _select_method(row, method_name: str):
    combo = row.method_combo
    for i in range(combo.count()):
        if method_name.lower() in combo.itemText(i).lower():
            combo.setCurrentIndex(i)
            return
    raise AssertionError(f"Method {method_name!r} not in combo box")


# ══════════════════════════════════════════════════════════════════
# Layer 1 — spinbox follows engine when cap inflates customer_charged
# ══════════════════════════════════════════════════════════════════

class TestLayer1SpinboxSyncsToEngine:
    """The screenshot scenario: $300 order, $100 cap, SNAP charge typed
    as $150.  After _update_summary the spinbox MUST display $200 — the
    engine's cap-inflated customer_charged — not the user's typed value."""

    def test_cap_inflation_writes_back_to_spinbox(self, qtbot, capped_db):
        from fam.ui.payment_screen import PaymentScreen

        order_id = _make_order(capped_db, 30000)  # $300
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        # Single SNAP row charge=$150; with $100 cap on a $300 order the
        # engine inflates customer_charged to $200.
        row = screen._payment_rows[0]
        _select_method(row, 'SNAP')
        row._set_active_charge(15000)  # $150
        row._recompute()
        screen._on_row_changed()

        assert row._get_active_charge() == 20000, (
            "Layer 1 broken: spinbox should auto-update to $200 once "
            "the engine inflates customer_charged due to the $100 cap "
            f"(spinbox actually has {row._get_active_charge()} cents)"
        )

    def test_no_cap_leaves_typed_charge_unchanged(self, qtbot, capped_db):
        """Sanity: when the cap doesn't bind, the user's typed charge
        must NOT be overwritten."""
        from fam.ui.payment_screen import PaymentScreen

        # Below the $100 cap: typed $50 SNAP, uncapped match $50, no cap
        order_id = _make_order(capped_db, 10000)  # $100
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, 'SNAP')
        row._set_active_charge(5000)  # $50
        row._recompute()
        screen._on_row_changed()

        assert row._get_active_charge() == 5000, (
            "Non-capped charge must survive _update_summary unchanged "
            f"(spinbox actually has {row._get_active_charge()} cents)"
        )

    def test_overtyped_charge_clamps_down_to_engine(self, qtbot, capped_db):
        """If the volunteer manually types a charge HIGHER than what the
        engine would set, _update_summary must still bring the spinbox
        to the canonical engine value — drift in either direction is
        equally dangerous."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _make_order(capped_db, 30000)  # $300
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, 'SNAP')
        # Overshoot — type $250 SNAP on a $300 order with $100 cap.
        # The engine clamps method_amount at receipt_total ($300) and
        # then computes customer_charged after applying the cap.
        row._set_active_charge(25000)
        row._recompute()
        screen._on_row_changed()

        # Engine settles at $200 customer pays + $100 match = $300 method_amount.
        assert row._get_active_charge() == 20000, (
            "Spinbox typed above the engine value must be brought back "
            "down to the canonical customer_charged "
            f"(actual: {row._get_active_charge()} cents)"
        )


# ══════════════════════════════════════════════════════════════════
# Layer 2 — confirm refuses on row/engine drift
# ══════════════════════════════════════════════════════════════════

class TestLayer2ConfirmGuard:
    """If any row's spinbox value disagrees with the engine's
    customer_charged at confirm time, _confirm_payment MUST refuse to
    save and surface the mismatch.  This is the financial-integrity
    backstop — any future regression of Layer 1 still cannot corrupt
    the database."""

    def test_drift_aborts_confirm(self, qtbot, capped_db, monkeypatch):
        """Force the spinbox to disagree with the engine and verify
        the confirm flow refuses to commit."""
        from fam.ui.payment_screen import PaymentScreen

        order_id = _make_order(capped_db, 30000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, 'SNAP')
        row._set_active_charge(15000)
        row._recompute()
        screen._on_row_changed()

        # Layer 1 should have brought the spinbox to $200 already.
        # Forcibly desync it back to $150 with signals blocked, mirroring
        # the bug class — engine still says $200, spinbox shows $150.
        row.blockSignals(True)
        try:
            row._set_active_charge(15000)
        finally:
            row.blockSignals(False)
        assert row._get_active_charge() == 15000

        # The confirm flow shows a confirmation dialog before saving.
        # We never want to reach it — Layer 2 should stop the flow at
        # the integrity check.  Patch QMessageBox.question so a dialog
        # call would fail the test.
        from PySide6.QtWidgets import QMessageBox

        def _fail_if_called(*args, **kwargs):
            raise AssertionError(
                "Layer 2 broken: _confirm_payment reached the QMessageBox "
                "dialog despite a row/engine charge mismatch")
        monkeypatch.setattr(QMessageBox, 'question', _fail_if_called)

        # Suppress the "rolled back" log spam — _show_error fires.
        screen._confirm_payment()

        # No DB rows should have been committed.
        rows = capped_db.execute(
            "SELECT COUNT(*) FROM payment_line_items").fetchone()
        assert rows[0] == 0, (
            "Layer 2 broken: payment_line_items contains rows even "
            "though confirm should have aborted on charge mismatch")
        # Confirm button must be re-enabled so the volunteer can retry.
        assert screen.confirm_btn.isEnabled()
        # Error message set on the error_label.  We don't check
        # isVisible() because Qt only marks descendants visible after
        # the parent screen is shown — instead we verify the label was
        # told to display, and inspect the content.
        msg = screen.error_label.text().lower()
        assert msg, "Layer 2 broken: error_label is empty after a tripped guard"
        assert 'mismatch' in msg or 'auto-distribute' in msg, (
            f"Layer 2 error message lacks actionable guidance: {msg!r}")

    def test_aligned_state_passes_guard(self, qtbot, capped_db, monkeypatch):
        """When the row matches the engine, confirm proceeds normally."""
        from fam.ui.payment_screen import PaymentScreen
        from PySide6.QtWidgets import QMessageBox

        order_id = _make_order(capped_db, 30000)
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, 'SNAP')
        row._set_active_charge(15000)
        row._recompute()
        screen._on_row_changed()

        # Layer 1 has brought the spinbox into agreement at $200.
        assert row._get_active_charge() == 20000

        # User confirms the collection dialog AND the post-save dialog.
        monkeypatch.setattr(QMessageBox, 'question',
                            lambda *a, **kw: QMessageBox.Yes)

        screen._confirm_payment()

        rows = capped_db.execute(
            "SELECT customer_charged, match_amount, method_amount "
            "FROM payment_line_items").fetchall()
        assert len(rows) == 1
        cust, match, ma = rows[0]
        # Saved values must match what the dialog promised.
        assert cust == 20000
        assert match == 10000
        assert ma == 30000


# ══════════════════════════════════════════════════════════════════
# Layer 3 — draft round-trip preserves cap-inflated charge
# ══════════════════════════════════════════════════════════════════

class TestLayer3DraftRoundTrip:
    """Save → reload of a cap-affected order must restore the spinbox
    to the engine's customer_charged, not to the under-cap value the
    legacy ``method_amount/(1+match%)`` formula would produce."""

    def test_set_data_uses_customer_charged_when_provided(self, qtbot, capped_db):
        from fam.ui.widgets.payment_row import PaymentRow

        # PaymentRow auto-loads methods from the market when market_id
        # is supplied — no separate populate call needed.
        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)

        # Saved row from a cap-affected draft: method_amount=$300,
        # match=$100, customer_charged=$200.
        row.set_data(
            payment_method_id=1,
            method_amount=30000,
            customer_charged=20000,
        )
        assert row._get_active_charge() == 20000, (
            "Layer 3 broken: set_data with explicit customer_charged "
            "should write 20000 to the spinbox — got "
            f"{row._get_active_charge()}")

    def test_set_data_legacy_path_unchanged(self, qtbot, capped_db):
        """Legacy callers that pass only method_amount must still get
        the inverse-formula charge — backward compatibility."""
        from fam.ui.widgets.payment_row import PaymentRow

        row = PaymentRow(market_id=1)
        qtbot.addWidget(row)

        # method_amount=$300, no customer_charged → derive 300/(1+1.0) = $150.
        row.set_data(payment_method_id=1, method_amount=30000)
        assert row._get_active_charge() == 15000, (
            "Legacy set_data path must still derive charge from "
            "method_amount when customer_charged is not provided")

    def test_full_save_then_reload_preserves_cap_inflated_charge(
            self, qtbot, capped_db, monkeypatch):
        """End-to-end: save a draft against a cap-affected order, then
        reload it and verify the spinbox shows the engine's customer
        charge, not the under-cap value."""
        from fam.ui.payment_screen import PaymentScreen
        from PySide6.QtWidgets import QMessageBox

        order_id = _make_order(capped_db, 30000)

        # ── Pass 1: enter, save as draft ──────────────────────────
        screen = PaymentScreen()
        qtbot.addWidget(screen)
        screen.load_customer_order(order_id)

        row = screen._payment_rows[0]
        _select_method(row, 'SNAP')
        row._set_active_charge(15000)
        row._recompute()
        screen._on_row_changed()
        # Layer 1 inflated to $200.
        assert row._get_active_charge() == 20000

        monkeypatch.setattr(QMessageBox, 'question',
                            lambda *a, **kw: QMessageBox.No)
        screen._save_draft()

        saved_row = capped_db.execute(
            "SELECT customer_charged, method_amount, match_amount "
            "FROM payment_line_items").fetchone()
        # sqlite3.Row supports indexed access; convert to tuple for diff
        saved = (saved_row['customer_charged'],
                 saved_row['method_amount'],
                 saved_row['match_amount'])
        assert saved == (20000, 30000, 10000), (
            f"Draft did not save cap-adjusted values: {saved}")

        # ── Pass 2: reload the order in a fresh screen ────────────
        screen2 = PaymentScreen()
        qtbot.addWidget(screen2)
        screen2.load_customer_order(order_id)

        # The reloaded row's spinbox must show $200 (the saved
        # customer_charged) — NOT $150 (which the legacy
        # method_amount/(1+match%) path would compute).
        reloaded_row = screen2._payment_rows[0]
        assert reloaded_row._get_active_charge() == 20000, (
            "Layer 3 broken: draft reload reverted the spinbox to the "
            "under-cap value — got "
            f"{reloaded_row._get_active_charge()} cents, expected 20000")


# ══════════════════════════════════════════════════════════════════
# Source-level guards (cheap, no Qt event loop)
# ══════════════════════════════════════════════════════════════════

class TestSourceLevelGuards:
    """Cheap sentinel tests so future refactors of the relevant
    methods can't silently drop the integrity layers."""

    def _confirm_source(self) -> str:
        import inspect
        import fam.ui.payment_screen as ps
        src = inspect.getsource(ps)
        marker = 'def _confirm_payment('
        start = src.find(marker)
        assert start != -1
        end = src.find('\n    def ', start + len(marker))
        return src[start:(end if end != -1 else len(src))]

    def _update_summary_source(self) -> str:
        """Return the body of the engine-driving routine.

        v2.0.1 split: ``_update_summary`` is now a tiny re-entry-guard
        wrapper that calls ``_update_summary_impl`` for the actual
        engine + write-back work.  This helper concatenates BOTH so
        the source-pin assertions (``_set_active_charge``,
        ``blockSignals``) remain valid regardless of which half they
        live in.
        """
        import inspect
        import fam.ui.payment_screen as ps
        src = inspect.getsource(ps)
        chunks = []
        for marker in ('def _update_summary(', 'def _update_summary_impl('):
            start = src.find(marker)
            if start == -1:
                continue
            end = src.find('\n    def ', start + len(marker))
            chunks.append(src[start:(end if end != -1 else len(src))])
        assert chunks, (
            "_update_summary or _update_summary_impl must exist on "
            "PaymentScreen")
        return '\n'.join(chunks)

    def test_confirm_has_charge_integrity_guard(self):
        src = self._confirm_source().lower()
        # Must contain a comparison of row charge against the engine's
        # customer_charged inside the confirm path.
        assert 'customer_charged' in src
        assert '_get_active_charge' in src, (
            "Layer 2 missing: _confirm_payment must read each row's "
            "live charge via _get_active_charge() to compare against "
            "the engine result")

    def test_update_summary_writes_back_customer_charged(self):
        src = self._update_summary_source()
        assert '_set_active_charge' in src, (
            "Layer 1 missing: _update_summary must call "
            "_set_active_charge to sync the spinbox to the engine's "
            "customer_charged when the cap inflates the user's typed "
            "value")
        # The write-back must be guarded against signal re-entry.
        assert 'blockSignals' in src

    def test_set_data_accepts_customer_charged(self):
        import inspect
        from fam.ui.widgets.payment_row import PaymentRow
        sig = inspect.signature(PaymentRow.set_data)
        assert 'customer_charged' in sig.parameters, (
            "Layer 3 missing: PaymentRow.set_data must accept a "
            "customer_charged parameter so draft restore preserves "
            "cap-inflated values")
