"""``_adjust_transaction`` must NOT shadow module-level imports
(v2.0.1 fix, 2026-05-05).

Regression: the v2.0.1 AdjustmentDialog re-fetch fix added
``from fam.models.transaction import get_transaction_by_id``
*inside* ``_adjust_transaction``, which Python promotes to a
function-local binding for the entire body.  The earlier reference
at the top of the function (``txn = get_transaction_by_id(txn_id)``)
then raised ``UnboundLocalError: cannot access local variable
'get_transaction_by_id' where it is not associated with a value``
because the local hadn't been assigned yet.

This file pins two things:

  1. **Static source pin** — no local ``from fam.models.transaction
     import`` lines inside ``_adjust_transaction`` (the module-level
     import already provides every name needed).
  2. **Runtime smoke test** — calling ``_adjust_transaction`` with
     a real txn_id reaches the dialog construction without raising
     UnboundLocalError, and rejecting the dialog returns cleanly.
"""

import inspect

import pytest


# ════════════════════════════════════════════════════════════════════
# 1. Static source pin
# ════════════════════════════════════════════════════════════════════


class TestNoLocalShadowImports:
    """Forbid function-local imports of names that are already imported
    at the module level — the Python-scoping footgun this regression
    came from."""

    def test_adjust_transaction_has_no_local_transaction_imports(self):
        from fam.ui.admin_screen import AdminScreen
        src = inspect.getsource(AdminScreen._adjust_transaction)
        # Reject any "from fam.models.transaction import ..." inside
        # the function body — module-level import already provides
        # get_transaction_by_id, update_transaction, etc.
        offenders = [
            ln.strip() for ln in src.splitlines()
            if 'from fam.models.transaction import' in ln
        ]
        assert offenders == [], (
            "Function-local imports of fam.models.transaction "
            "names inside _adjust_transaction shadow the module-level "
            "imports and cause UnboundLocalError on earlier "
            "references.  Offending lines:\n  " +
            "\n  ".join(offenders))

    def test_module_level_import_still_present(self):
        """Belt-and-suspenders: the names referenced inside
        ``_adjust_transaction`` must come from the module level."""
        import fam.ui.admin_screen as adm
        assert hasattr(adm, 'get_transaction_by_id')
        assert hasattr(adm, 'update_transaction')


# ════════════════════════════════════════════════════════════════════
# 2. Runtime smoke test
# ════════════════════════════════════════════════════════════════════


@pytest.fixture
def db_with_confirmed_txn(tmp_path):
    from fam.database.connection import (
        set_db_path, close_connection, get_connection,
    )
    from fam.database.schema import initialize_database
    from fam.models.customer_order import create_customer_order
    from fam.models.transaction import (
        create_transaction, save_payment_line_items, confirm_transaction,
    )

    db_file = str(tmp_path / "adjust_smoke.db")
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
        "(1, 'SNAP', 100.0, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        "payment_method_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'Vendor A')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1)")
    conn.execute(
        "INSERT INTO vendor_payment_methods (vendor_id, payment_method_id) "
        "VALUES (1, 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES (1, 1, '2099-05-01', 'Open', 'Tester')")
    conn.commit()

    order_id, _ = create_customer_order(
        market_day_id=1, customer_label='C-001-LB1')
    txn_id, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=1000,
        customer_order_id=order_id,
        market_day_date='2099-05-01')
    save_payment_line_items(txn_id, [{
        'payment_method_id': 1,
        'method_name_snapshot': 'SNAP',
        'match_percent_snapshot': 100.0,
        'method_amount': 1000, 'match_amount': 500,
        'customer_charged': 500, 'photo_path': None,
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
