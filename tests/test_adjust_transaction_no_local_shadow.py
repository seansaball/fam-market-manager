"""``_adjust_transaction`` must NOT shadow module-level imports
(v2.0.1 fix, 2026-05-05; v2.0.7 follow-up, 2026-05-07).

Regression history — same scoping footgun, three sites:

  * **v2.0.1** the AdjustmentDialog re-fetch fix added
    ``from fam.models.transaction import get_transaction_by_id``
    *inside* ``_adjust_transaction``.  Python promotes any name
    bound in a function body to a function-local for the WHOLE
    body, so the earlier reference (``txn =
    get_transaction_by_id(txn_id)``) raised
    ``UnboundLocalError`` immediately on entry.

  * **v2.0.7** the denomination-payment safety gate added
    ``from fam.models.audit import log_action`` *inside* a
    conditional branch (``if denom_methods:``).  The first
    reference was the call right after the import (also inside
    the conditional), so the v2.0.1 source pin and the broader
    AST-based "first_ref < import_line" check both passed.  When
    the gate didn't fire (Cash-only / SNAP-only adjustment),
    Python still treated ``log_action`` as a function-local
    because of the conditional binding, so later references in
    the save path raised ``UnboundLocalError: cannot access
    local variable 'log_action'`` and the user saw "Adjustment
    failed: ..." — with no useful surface to the volunteer.

This file pins three things:

  1. **Static source pin** — no local imports of names that
     re-shadow ``fam.models.transaction`` OR ``fam.models.audit``
     symbols inside ``_adjust_transaction``.
  2. **Runtime smoke test (entry path)** — clicking Adjust on a
     non-denom transaction reaches the dialog without raising.
  3. **Runtime smoke test (save path)** — accepting the dialog
     on a non-denom transaction completes the SAVE path without
     raising.  This is the v2.0.7 path the v2.0.1 entry-only
     test missed entirely.

The broader AST-level "no function-local import shadows a
module-level binding" rule lives at
``tests/test_codebase_hygiene.py::TestNoUnboundLocalShadows`` —
that's the codebase-wide net.  This file pins the two specific
adjustment hotpaths the user has hit twice now."""

import inspect

import pytest


# ════════════════════════════════════════════════════════════════════
# 1. Static source pin
# ════════════════════════════════════════════════════════════════════


class TestNoLocalShadowImports:
    """Forbid function-local imports of names that are already imported
    at the module level — the Python-scoping footgun this regression
    came from."""

    @staticmethod
    def _local_import_lines(src: str, module_path: str) -> list:
        """Return non-comment lines that look like
        ``from <module_path> import ...`` inside the given source
        body.  Filters comments so the pin's own warning comments
        (which mention the forbidden form to explain WHY) don't
        register as offenders."""
        wanted = f'from {module_path} import'
        offenders = []
        for ln in src.splitlines():
            stripped = ln.strip()
            if stripped.startswith('#'):
                continue
            if stripped.startswith(wanted):
                offenders.append(stripped)
        return offenders

    def test_adjust_transaction_has_no_local_transaction_imports(self):
        from fam.ui.admin_screen import AdminScreen
        src = inspect.getsource(AdminScreen._adjust_transaction)
        offenders = self._local_import_lines(
            src, 'fam.models.transaction')
        assert offenders == [], (
            "Function-local imports of fam.models.transaction "
            "names inside _adjust_transaction shadow the module-level "
            "imports and cause UnboundLocalError on earlier "
            "references.  Offending lines:\n  " +
            "\n  ".join(offenders))

    def test_adjust_transaction_has_no_local_audit_imports(self):
        """v2.0.7 regression pin: the denomination safety-gate added
        ``from fam.models.audit import log_action`` inside a
        conditional branch.  When the gate didn't fire (Cash-only
        adjustment), Python still treated ``log_action`` as a
        function-local for the whole body, so the save-path
        references raised ``UnboundLocalError`` and the volunteer
        saw "Adjustment failed: cannot access local variable
        'log_action'"."""
        from fam.ui.admin_screen import AdminScreen
        src = inspect.getsource(AdminScreen._adjust_transaction)
        offenders = self._local_import_lines(
            src, 'fam.models.audit')
        assert offenders == [], (
            "Function-local imports of fam.models.audit names "
            "inside _adjust_transaction shadow the module-level "
            "import (line 21) and cause UnboundLocalError on the "
            "save-path references when the conditional binding "
            "branch is skipped.  Offending lines:\n  " +
            "\n  ".join(offenders))

    def test_module_level_import_still_present(self):
        """Belt-and-suspenders: the names referenced inside
        ``_adjust_transaction`` must come from the module level."""
        import fam.ui.admin_screen as adm
        assert hasattr(adm, 'get_transaction_by_id')
        assert hasattr(adm, 'update_transaction')
        assert hasattr(adm, 'log_action')


# ════════════════════════════════════════════════════════════════════
# 2. Runtime smoke test
# ════════════════════════════════════════════════════════════════════


def _build_db_with_txn(tmp_path, *, method_name, denomination,
                        receipt_total, customer_charged):
    """Shared fixture body — creates a DB with one confirmed
    transaction using the specified payment method.

    ``denomination`` of None creates a non-denominated method (SNAP,
    Cash); a positive int (e.g. 1000) creates a denominated method
    (Food RX 2 tokens at $10 each).  The save-path runtime test
    parametrises on this so we cover BOTH the non-denom path
    (which bypasses the v2.0.7 safety gate) and the denom path
    (which goes through it)."""
    from fam.database.connection import (
        set_db_path, close_connection, get_connection,
    )
    from fam.database.schema import initialize_database
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import (
        create_transaction, save_payment_line_items, confirm_transaction,
    )

    db_file = str(tmp_path / f"adjust_smoke_{method_name}.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "(1, ?, 100.0, ?, 1, 1)",
        (method_name, denomination))
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'Vendor A'), (2, 'Vendor B')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1), (1, 2)")
    conn.execute(
        "INSERT INTO vendor_payment_methods (vendor_id, payment_method_id) "
        "VALUES (1, 1), (2, 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES (1, 1, '2099-05-01', 'Open', 'Tester')")
    conn.commit()

    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-001-LB1')
    txn_id, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=receipt_total,
        customer_order_id=order_id,
        market_day_date='2099-05-01')
    match_amount = receipt_total - customer_charged
    save_payment_line_items(txn_id, [{
        'payment_method_id': 1,
        'method_name_snapshot': method_name,
        'match_percent_snapshot': 100.0,
        'method_amount': receipt_total,
        'match_amount': match_amount,
        'customer_charged': customer_charged,
        'photo_path': None,
    }])
    confirm_transaction(txn_id, confirmed_by='Tester')
    return conn, txn_id


@pytest.fixture
def db_with_confirmed_txn(tmp_path):
    """Default fixture: SNAP $5 (charge) → $5 match, $10 receipt."""
    conn, txn_id = _build_db_with_txn(
        tmp_path, method_name='SNAP', denomination=None,
        receipt_total=1000, customer_charged=500)
    yield conn, txn_id
    from fam.database.connection import close_connection
    close_connection()


@pytest.fixture
def db_with_cash_txn(tmp_path):
    """Cash-only $10 transaction — the user's exact reproducer.
    Cash has 0% match, so customer_charged == receipt_total = $10."""
    from fam.database.connection import (
        set_db_path, close_connection, get_connection,
    )
    from fam.database.schema import initialize_database
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import (
        create_transaction, save_payment_line_items, confirm_transaction,
    )

    db_file = str(tmp_path / "adjust_smoke_cash.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "(1, 'Cash', 0.0, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'Vendor A'), (2, 'Vendor B')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1), (1, 2)")
    conn.execute(
        "INSERT INTO vendor_payment_methods (vendor_id, payment_method_id) "
        "VALUES (1, 1), (2, 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES (1, 1, '2099-05-01', 'Open', 'Tester')")
    conn.commit()

    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-002-LB1')
    txn_id, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=1000,
        customer_order_id=order_id,
        market_day_date='2099-05-01')
    save_payment_line_items(txn_id, [{
        'payment_method_id': 1,
        'method_name_snapshot': 'Cash',
        'match_percent_snapshot': 0.0,
        'method_amount': 1000,
        'match_amount': 0,
        'customer_charged': 1000,
        'photo_path': None,
    }])
    confirm_transaction(txn_id, confirmed_by='Tester')
    yield conn, txn_id
    close_connection()


class TestAdjustTransactionRuntimeSmoke:

    def test_clicking_adjust_does_not_raise_unbound_local_error(
            self, qtbot, monkeypatch, db_with_confirmed_txn):
        """Reproduces the user-reported v2.0.1 hotpath.  Pre-fix this
        raised ``UnboundLocalError: cannot access local variable
        'get_transaction_by_id'`` immediately on entry to
        ``_adjust_transaction``."""
        from fam.ui.admin_screen import AdminScreen
        from PySide6.QtWidgets import QDialog

        _, txn_id = db_with_confirmed_txn

        screen = AdminScreen()
        qtbot.addWidget(screen)

        # Auto-cancel the AdjustmentDialog so the test doesn't hang
        # on the modal exec().  We're only verifying that the entry
        # path (the get_transaction_by_id lookup that raised) works.
        monkeypatch.setattr(
            'fam.ui.admin_screen.AdjustmentDialog.exec',
            lambda self: QDialog.Rejected,
        )

        # Should NOT raise.  The pre-fix behaviour was an immediate
        # UnboundLocalError before the dialog was even constructed.
        screen._adjust_transaction(txn_id)


class TestAdjustTransactionSavePathRuntimeSmoke:
    """v2.0.7 regression pin — exercise the SAVE path on a non-denom
    transaction.  This is the path that raised
    ``UnboundLocalError: cannot access local variable 'log_action'``
    in the user-reported reproducer:

        1. Volunteer makes a $10 Cash transaction.
        2. Volunteer clicks Adjust to change it to $20.
        3. Save raises UnboundLocalError because the v2.0.7
           denom-safety-gate's ``from fam.models.audit import
           log_action`` shadowed the module-level ``log_action`` for
           the entire function body, even on Cash adjustments where
           the gate didn't fire.

    The pre-existing entry-only test (above) reached only the dialog
    construction step.  These tests reach the save path: they
    monkeypatch ``AdjustmentDialog.exec`` to return ``Accepted`` and
    populate the dialog's outputs so the function actually walks the
    save block where ``log_action`` is referenced."""

    def _accept_dialog_with_changed_vendor(
            self, monkeypatch, new_vendor_id):
        """Make ``AdjustmentDialog.exec`` return Accepted AND switch
        the dialog's vendor combo to ``new_vendor_id`` so the save
        block's vendor-change branch (line ~2113) executes a
        ``log_action(...)`` call — exactly the same call shape that
        raised UnboundLocalError pre-fix.

        Why vendor-change rather than total-change: the
        ``Payment Mismatch`` guard at line ~2025 returns early when
        ``allocated_total != new_total_cents`` (typing a new total
        without re-typing payment amounts triggers it).  Changing
        the vendor leaves totals balanced so the save proceeds to
        the ``log_action`` call we need to exercise.

        ``_adjust_transaction`` reads dialog widget values DIRECTLY
        (``dialog.receipt_spin.value()``, ``dialog.vendor_combo``
        etc.) — not via getter methods.  So we have to mutate the
        widget itself, not patch a getter."""
        from PySide6.QtWidgets import QDialog, QMessageBox
        from fam.ui.admin_screen import AdjustmentDialog

        def patched_exec(self):
            # Find the new vendor's index in the combo and select it.
            for i in range(self.vendor_combo.count()):
                if self.vendor_combo.itemData(i) == new_vendor_id:
                    self.vendor_combo.setCurrentIndex(i)
                    break
            return QDialog.Accepted

        monkeypatch.setattr(AdjustmentDialog, 'exec', patched_exec)
        # Suppress any QMessageBox.warning so the test doesn't hang
        # if a guard fires unexpectedly.  Default Yes so any "are
        # you sure?" prompts proceed.
        monkeypatch.setattr(
            QMessageBox, 'warning',
            lambda parent, title, text, *a, **k: QMessageBox.Yes,
        )
        monkeypatch.setattr(
            QMessageBox, 'information',
            lambda parent, title, text, *a, **k: None,
        )

    def test_save_path_on_cash_adjustment_does_not_unbound_local(
            self, qtbot, monkeypatch, db_with_cash_txn):
        """User's reproducer: $10 Cash transaction adjusted (vendor
        change rather than total change to keep totals balanced and
        bypass the Payment Mismatch early-return).  Pre-fix this
        raised UnboundLocalError on ``log_action`` because the
        v2.0.7 denom-safety-gate's ``from fam.models.audit import
        log_action`` shadowed the module-level binding for the
        whole function body, even on Cash adjustments where the
        gate didn't fire."""
        from fam.ui.admin_screen import AdminScreen

        _, txn_id = db_with_cash_txn
        self._accept_dialog_with_changed_vendor(
            monkeypatch, new_vendor_id=2)

        # Suppress the QMessageBox.critical so the test doesn't
        # show a dialog if something unexpected goes wrong.  We
        # capture any error dialog text instead.
        seen_errors = []
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(
            QMessageBox, 'critical',
            lambda parent, title, text, *a, **k: seen_errors.append(
                (title, text)),
        )

        screen = AdminScreen()
        qtbot.addWidget(screen)

        # Pre-fix: this raised UnboundLocalError, the broad
        # exception handler caught it, and the user saw
        # "Adjustment failed: cannot access local variable
        # 'log_action'..." — the test confirms NO such error
        # surfaces.
        screen._adjust_transaction(txn_id)

        log_action_errors = [
            e for e in seen_errors
            if 'log_action' in (e[1] or '')]
        assert log_action_errors == [], (
            "Adjustment save path raised UnboundLocalError on "
            "'log_action' — same scoping footgun as v2.0.1 (see "
            "TestNoLocalShadowImports above).  Captured error "
            "dialogs: " + repr(seen_errors))

    def test_save_path_on_snap_adjustment_does_not_unbound_local(
            self, qtbot, monkeypatch, db_with_confirmed_txn):
        """SNAP-only adjustment also bypasses the v2.0.7 denom-method
        safety gate.  Same UnboundLocalError surfaces if the
        local-import shadow is reintroduced."""
        from fam.ui.admin_screen import AdminScreen

        _, txn_id = db_with_confirmed_txn
        self._accept_dialog_with_changed_vendor(
            monkeypatch, new_vendor_id=2)

        seen_errors = []
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(
            QMessageBox, 'critical',
            lambda parent, title, text, *a, **k: seen_errors.append(
                (title, text)),
        )

        screen = AdminScreen()
        qtbot.addWidget(screen)
        screen._adjust_transaction(txn_id)

        log_action_errors = [
            e for e in seen_errors
            if 'log_action' in (e[1] or '')]
        assert log_action_errors == [], (
            "SNAP adjustment save path raised UnboundLocalError on "
            "'log_action'.  Captured error dialogs: "
            + repr(seen_errors))
