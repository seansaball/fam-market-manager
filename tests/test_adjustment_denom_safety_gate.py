"""Adjustment safety gate for denominated payments (v2.0.7).

Policy: rather than chase every denomination edge case in the
Adjustment flow (which has surfaced multiple data-integrity
bugs — denom snap drift, multi-receipt allocation mismatch,
Phase B customer-side forfeit non-alignment), gate the entry
point.  When the volunteer clicks Adjust on a transaction that
includes denominated payment methods (Food RX, JH Food Bucks,
FMNP), surface a clear warning that:

  * Names the offending methods explicitly
  * Calls out multi-receipt single-vendor shape as additional risk
  * Recommends Void → recreate as the safer path
  * Provides a one-click "Void Instead" button that triggers void
  * Allows "Adjust Anyway" override with audit-log entry

This test pins:
  1. The detection helper returns the right shape
  2. The user-facing dialog wires the recommended void path
  3. Override paths are logged for traceability
"""

import inspect

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_adj_gate.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield get_connection()
    close_connection()


def _seed(conn, with_denom_pli=True, multi_receipt=False):
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 10000, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (42, 'V')")
    conn.execute(
        "INSERT INTO payment_methods "
        "(id, name, match_percent, is_active, sort_order, "
        " denomination) VALUES "
        "(1, 'JH Food Bucks', 100.0, 1, 1, 200), "
        "(2, 'SNAP', 100.0, 1, 2, NULL), "
        "(3, 'Food RX', 100.0, 1, 3, 1000)")
    conn.execute(
        "INSERT INTO market_days "
        "(id, market_id, date, status, opened_by) VALUES "
        "(1, 1, '2026-05-06', 'Open', 'T')")
    conn.execute(
        "INSERT INTO customer_orders "
        "(id, market_day_id, customer_label, status, created_at) "
        "VALUES (99, 1, 'C-001-LB1', 'Confirmed', "
        "        '2026-05-06 12:00:00')")
    conn.execute(
        "INSERT INTO transactions "
        "(id, fam_transaction_id, market_day_id, vendor_id, "
        " receipt_total, status, customer_order_id, created_at) "
        "VALUES (101, 'FAM-T-101', 1, 42, 1000, 'Confirmed', 99, "
        "        '2026-05-06 12:00:00')")
    if multi_receipt:
        conn.execute(
            "INSERT INTO transactions "
            "(id, fam_transaction_id, market_day_id, vendor_id, "
            " receipt_total, status, customer_order_id, created_at) "
            "VALUES (102, 'FAM-T-102', 1, 42, 500, 'Confirmed', "
            "        99, '2026-05-06 12:00:00')")
    if with_denom_pli:
        # Denom row attached
        conn.execute(
            "INSERT INTO payment_line_items "
            "(transaction_id, payment_method_id, "
            " method_name_snapshot, match_percent_snapshot, "
            " method_amount, match_amount, customer_charged) "
            "VALUES (101, 1, 'JH Food Bucks', 100.0, 400, 200, 200)")
    else:
        # Non-denom only
        conn.execute(
            "INSERT INTO payment_line_items "
            "(transaction_id, payment_method_id, "
            " method_name_snapshot, match_percent_snapshot, "
            " method_amount, match_amount, customer_charged) "
            "VALUES (101, 2, 'SNAP', 100.0, 1000, 500, 500)")
    conn.commit()


# ──────────────────────────────────────────────────────────────────
# 1. Detection helper
# ──────────────────────────────────────────────────────────────────


class TestDetectAdjustmentRisk:

    def test_denom_pli_detected(self, qtbot, fresh_db):
        from fam.ui.admin_screen import AdminScreen
        from fam.models.transaction import get_transaction_by_id
        _seed(fresh_db, with_denom_pli=True)

        screen = AdminScreen()
        qtbot.addWidget(screen)
        txn = get_transaction_by_id(101)

        denom_methods, sibling_count = (
            screen._detect_adjustment_risk(txn))
        assert denom_methods == {'JH Food Bucks'}
        assert sibling_count == 1

    def test_non_denom_pli_returns_empty_set(self, qtbot, fresh_db):
        from fam.ui.admin_screen import AdminScreen
        from fam.models.transaction import get_transaction_by_id
        _seed(fresh_db, with_denom_pli=False)

        screen = AdminScreen()
        qtbot.addWidget(screen)
        txn = get_transaction_by_id(101)

        denom_methods, _ = screen._detect_adjustment_risk(txn)
        assert denom_methods == set(), (
            "SNAP-only transactions should not trigger the gate.")

    def test_multi_receipt_sibling_count(self, qtbot, fresh_db):
        from fam.ui.admin_screen import AdminScreen
        from fam.models.transaction import get_transaction_by_id
        _seed(fresh_db, with_denom_pli=True, multi_receipt=True)

        screen = AdminScreen()
        qtbot.addWidget(screen)
        txn = get_transaction_by_id(101)

        denom_methods, sibling_count = (
            screen._detect_adjustment_risk(txn))
        assert denom_methods == {'JH Food Bucks'}
        assert sibling_count == 2, (
            "Order has two transactions at the same vendor → "
            "sibling_count == 2.")


# ──────────────────────────────────────────────────────────────────
# 2. Source-pin: dialog wires Void Instead + override path
# ──────────────────────────────────────────────────────────────────


class TestAdjustmentRiskDialogWiring:

    def test_adjust_handler_calls_detection_helper(self):
        import fam.ui.admin_screen as adm
        src = inspect.getsource(adm.AdminScreen._adjust_transaction)
        assert '_detect_adjustment_risk' in src, (
            "_adjust_transaction must call _detect_adjustment_risk "
            "before opening AdjustmentDialog.")

    def test_dialog_offers_void_instead_button(self):
        import fam.ui.admin_screen as adm
        src = inspect.getsource(adm.AdminScreen._adjust_transaction)
        assert 'Void Instead' in src, (
            "Risk dialog must offer a 'Void Instead' button as the "
            "recommended path (one-click triggers void flow).")
        assert 'Adjust Anyway' in src, (
            "Risk dialog must offer 'Adjust Anyway' override for "
            "informed users — the gate is a warning, not a hard "
            "block.")

    def test_override_path_is_audit_logged(self):
        """When the user clicks Adjust Anyway, the override must
        write an audit-log entry so a future reconciliation issue
        can be traced to a deliberate decision."""
        import fam.ui.admin_screen as adm
        src = inspect.getsource(adm.AdminScreen._adjust_transaction)
        assert 'ADJUST_OVERRIDE' in src, (
            "Override path must emit an ADJUST_OVERRIDE audit row.")
        assert "log_action" in src, (
            "Override path must call log_action.")

    def test_void_instead_routes_to_void_handler(self):
        """The Void Instead button must trigger the existing
        ``_void_transaction`` flow — not duplicate the void logic."""
        import fam.ui.admin_screen as adm
        src = inspect.getsource(adm.AdminScreen._adjust_transaction)
        assert 'self._void_transaction(txn_id)' in src, (
            "Void Instead must call _void_transaction(txn_id) so "
            "the existing audit + cache-cleanup path runs.")


# ──────────────────────────────────────────────────────────────────
# 3. Voided transactions still blocked outright (unchanged)
# ──────────────────────────────────────────────────────────────────


class TestVoidedTransactionsStillBlocked:
    """Pre-existing behavior: voided transactions can never be
    adjusted.  The new safety gate must NOT change this."""

    def test_voided_txn_blocked_before_risk_check(self, qtbot, fresh_db):
        """Voided gate fires first — no risk dialog appears for
        voided transactions."""
        import fam.ui.admin_screen as adm
        src = inspect.getsource(adm.AdminScreen._adjust_transaction)
        # Sanity: the Voided check appears before the risk check
        voided_idx = src.find("'Voided'")
        risk_idx = src.find('_detect_adjustment_risk')
        assert voided_idx > 0
        assert risk_idx > 0
        assert voided_idx < risk_idx, (
            "Voided check must run BEFORE the denom-risk gate so "
            "voided transactions surface 'Cannot Adjust' rather "
            "than the more-permissive risk dialog.")
