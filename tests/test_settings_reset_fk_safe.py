"""Settings → Reset must succeed even with reward_rules configured
(v1.9.10 follow-up, 2026-05-01).

Onsite report: a manager hit Settings → Reset Data with one or
more reward rules configured.  The reset DELETE'd children in the
documented order but **forgot reward_rules and generated_rewards**.
When the reset reached ``DELETE FROM payment_methods`` the engine
raised:

    FOREIGN KEY constraint failed

…because ``reward_rules.source_method_id`` and
``reward_method_id`` reference payment_methods, and the rules
were still alive.  The reset halted leaving the DB in a partially
wiped state (audit_log, payment_line_items, fmnp_entries,
transactions, customer_orders, market_days were already gone —
but markets/vendors/payment_methods stayed).  No way out from the
UI; the user had to delete the DB file manually.

Fix: drain ``generated_rewards`` and ``reward_rules`` BEFORE
their parent payment_methods, run the whole sequence inside a
single transaction so a partial failure rolls back, and re-seed
the system-managed Unallocated Funds method afterwards so the
customer-gone branch keeps working post-reset without manual
re-config.
"""

import pytest

from fam.database.connection import (
    get_connection, set_db_path, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def db_with_reward_rule(tmp_path):
    """A DB shaped like a real install: market, vendor, methods,
    one reward rule, and a confirmed transaction.  This is the
    minimum repro of the FK-failure path."""
    db_file = str(tmp_path / "reset_repro.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) VALUES (1, 1)")
    # Two methods so reward_rules has BOTH source_method_id and
    # reward_method_id valid (Food RX is denominated, qualifies).
    conn.execute(
        "INSERT INTO payment_methods (name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "('SNAP', 100.0, NULL, 1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (name, match_percent, "
        "denomination, sort_order, is_active) VALUES "
        "('Food RX', 100.0, 1000, 2, 1)")
    snap_id = conn.execute(
        "SELECT id FROM payment_methods WHERE name='SNAP'"
    ).fetchone()[0]
    rx_id = conn.execute(
        "SELECT id FROM payment_methods WHERE name='Food RX'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO market_payment_methods (market_id, "
        "payment_method_id) VALUES (1, ?), (1, ?)", (snap_id, rx_id))
    conn.execute(
        "INSERT INTO vendor_payment_methods (vendor_id, "
        "payment_method_id) VALUES (1, ?), (1, ?)", (snap_id, rx_id))
    # The reward rule that triggers the FK failure on reset.
    conn.execute(
        "INSERT INTO reward_rules (source_method_id, threshold_cents, "
        "reward_method_id, reward_unit_cents, is_active) "
        "VALUES (?, 1000, ?, 200, 1)", (snap_id, rx_id))
    # And a market day + transaction so the other DELETE branches
    # also see real rows.
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES (1, 1, '2026-05-01', 'Open', 'Tester')")
    conn.commit()

    from fam.models.transaction import (
        create_transaction, save_payment_line_items,
        confirm_transaction,
    )
    txn_id, _ = create_transaction(
        market_day_id=1, vendor_id=1, receipt_total=2000,
        market_day_date='2026-05-01')
    save_payment_line_items(txn_id, [{
        'payment_method_id': snap_id,
        'method_name_snapshot': 'SNAP',
        'match_percent_snapshot': 100.0,
        'method_amount': 2000, 'match_amount': 1000,
        'customer_charged': 1000, 'photo_path': None,
    }])
    confirm_transaction(txn_id, confirmed_by='Tester')
    yield conn
    close_connection()


# ════════════════════════════════════════════════════════════════════
# 1. Reset succeeds with reward_rules + transaction + market data
# ════════════════════════════════════════════════════════════════════


class TestResetSucceedsWithRewardsConfigured:

    def test_reset_clears_all_tables_without_fk_error(
            self, db_with_reward_rule, qtbot, monkeypatch):
        """The reset path must execute every DELETE without raising
        a FOREIGN KEY error.  This was the bug: reward_rules
        referenced payment_methods, so DELETE FROM payment_methods
        failed if rules existed."""
        from fam.ui.settings_screen import SettingsScreen
        from PySide6.QtWidgets import QMessageBox, QInputDialog

        # Auto-confirm both warning dialogs.
        monkeypatch.setattr(QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QMessageBox, 'critical',
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QMessageBox, 'information',
                            staticmethod(lambda *a, **k: QMessageBox.Ok))
        # v2.0.1: Reset path also asks the operator to type RESET.
        monkeypatch.setattr(
            QInputDialog, 'getText',
            staticmethod(lambda *a, **k: ('RESET', True)))

        screen = SettingsScreen()
        qtbot.addWidget(screen)

        # Should NOT raise — pre-fix this raised
        # "FOREIGN KEY constraint failed" inside _reset_to_default.
        screen._reset_to_default()

        # Verify EVERY user table is empty.
        conn = db_with_reward_rule
        for table in (
                'generated_rewards', 'reward_rules',
                'audit_log', 'payment_line_items',
                'fmnp_entries', 'transactions', 'customer_orders',
                'market_days', 'market_payment_methods',
                'market_vendors', 'vendor_payment_methods',
                'vendors', 'markets'):
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0, (
                f"Reset must leave {table} empty, "
                f"got {count} row(s)")

        # ``payment_methods`` is allowed to retain the system-
        # managed Unallocated Funds row (re-seeded after reset so
        # the customer-gone branch keeps working).  Anything else
        # is a defect.
        non_system = conn.execute(
            "SELECT COUNT(*) FROM payment_methods "
            "WHERE COALESCE(is_system, 0) = 0"
        ).fetchone()[0]
        assert non_system == 0, (
            f"Reset must clear non-system payment methods; "
            f"got {non_system} non-system rows")

    def test_reset_is_atomic_under_partial_failure(
            self, db_with_reward_rule, qtbot, monkeypatch):
        """A failure mid-reset must roll back so the DB doesn't
        get stuck in a half-wiped state.  Pre-fix the lack of
        BEGIN/COMMIT meant the FK failure left the DB with
        markets/vendors/payment_methods alive but
        transactions/customer_orders/market_days gone — an
        unrecoverable mid-state from the UI.
        """
        from fam.ui.settings_screen import SettingsScreen
        from PySide6.QtWidgets import QMessageBox, QInputDialog
        import fam.ui.settings_screen as ss

        # Force a deliberate failure midway through the reset by
        # patching one of the model functions called inside the
        # try-block.  The transaction must roll back so the DB is
        # functionally restored.
        original_get_conn = ss.get_connection
        injected = {'count': 0}

        class FailingConn:
            def __init__(self, real):
                self._real = real
            def execute(self, sql, *a, **k):
                if 'DELETE FROM customer_orders' in sql:
                    raise RuntimeError('injected failure')
                return self._real.execute(sql, *a, **k)
            def commit(self):
                return self._real.commit()
            def __getattr__(self, name):
                return getattr(self._real, name)

        real_conn = original_get_conn()
        wrapped = FailingConn(real_conn)
        monkeypatch.setattr(ss, 'get_connection', lambda: wrapped)
        monkeypatch.setattr(QMessageBox, 'warning',
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QMessageBox, 'critical',
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QMessageBox, 'information',
                            staticmethod(lambda *a, **k: QMessageBox.Ok))
        monkeypatch.setattr(
            QInputDialog, 'getText',
            staticmethod(lambda *a, **k: ('RESET', True)))

        screen = SettingsScreen()
        qtbot.addWidget(screen)
        screen._reset_to_default()

        # The DB should be UNCHANGED — markets, vendors,
        # transactions, reward_rules all still present.
        markets = real_conn.execute(
            "SELECT COUNT(*) FROM markets").fetchone()[0]
        rules = real_conn.execute(
            "SELECT COUNT(*) FROM reward_rules").fetchone()[0]
        txns = real_conn.execute(
            "SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert markets == 1, (
            "atomic reset failed: markets table was partially "
            "cleared after an injected mid-reset error")
        assert rules == 1
        assert txns == 1
