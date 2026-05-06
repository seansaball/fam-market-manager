"""Regression: Vendor Settings tab should show a payment-method
eligibility matrix (✓/✗ columns) at-a-glance instead of forcing the
manager to click the per-vendor "Methods" button to inspect each
configuration.

User-reported (2026-04-30):

    "In the vendor settings tab can we add columns for each payment
     type and give a check mark or x like we do on the payment
     screen so we can see at a glance who is configured to accept
     what instead of needing to click the methods button for every
     single one"

Pinned behavior:

  * One column per ACTIVE, non-system payment method, inserted
    between the "Active" column and the "Actions" cell.
  * Cell text:  "✓" (green, ACCENT_GREEN) when the vendor is
    eligible; "✗" (red, ERROR_COLOR) when not.
  * Cells are center-aligned to match the Payment screen's
    breakdown table style.
  * Header tooltip on each method column explains match%,
    denomination, and the ✓/✗ legend.
  * The "Methods" button still works for editing — and after the
    user accepts the dialog, the table refreshes so the new ✓/✗
    state is visible immediately.
  * Toggling a payment method active/inactive in the Payment
    Methods tab also re-renders the Vendors tab (column set
    follows ``active_only=True``).
"""
import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def matrix_db(tmp_path, monkeypatch):
    """Fresh DB seeded with 2 vendors × 3 active payment methods.

    Vendor 1 accepts SNAP + Cash (not FMNP).
    Vendor 2 accepts SNAP only.
    Plus an inactive 5th method to verify it does NOT appear as a
    column.
    """
    db_file = str(tmp_path / "vmm.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    # Markets / vendors
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name, is_active) "
        "VALUES (1, 'Apple Stand', 1)")
    conn.execute(
        "INSERT INTO vendors (id, name, is_active) "
        "VALUES (2, 'Bakery', 1)")
    # 4 active + 1 inactive payment methods.  Use names that don't
    # collide with seed in case initialize_database pre-seeded any.
    # (Schema seeding is idempotent on duplicate names — existing
    # rows stay; our INSERTs use unique IDs starting at 100 to
    # sidestep any seed-time collisions.)
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active) "
        "VALUES (101, 'TestSNAP', 100.0, 1, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active) "
        "VALUES (102, 'TestCash', 0.0, 2, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active) "
        "VALUES (103, 'TestFMNP', 100.0, 3, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active) "
        "VALUES (104, 'OldRetiredMethod', 100.0, 99, 0)")
    # Vendor eligibility — vendor 1 = SNAP + Cash, vendor 2 = SNAP.
    conn.execute(
        "INSERT INTO vendor_payment_methods "
        "(vendor_id, payment_method_id) VALUES (1, 101)")
    conn.execute(
        "INSERT INTO vendor_payment_methods "
        "(vendor_id, payment_method_id) VALUES (1, 102)")
    conn.execute(
        "INSERT INTO vendor_payment_methods "
        "(vendor_id, payment_method_id) VALUES (2, 101)")
    conn.commit()
    yield conn
    close_connection()


def _seed_methods_in_screen(screen):
    """Helper — settings screen reads global state, so just call its
    own _load_vendors() after the fixture has populated the DB."""
    screen._load_vendors()


def _header_labels(table):
    return [
        table.horizontalHeaderItem(c).text()
        for c in range(table.columnCount())
    ]


class TestVendorSettingsMethodMatrix:

    def test_method_columns_present_between_active_and_actions(
            self, qtbot, matrix_db):
        """Layout: ID | Name | Contact | CPT | ACH | Active |
        <method-cols> | Actions."""
        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        _seed_methods_in_screen(screen)
        labels = _header_labels(screen.vendors_table)
        # Static prefix.
        assert labels[:6] == [
            "ID", "Name", "Contact", "Check Payable To",
            "ACH", "Active",
        ]
        # Last column is always Actions.
        assert labels[-1] == "Actions"
        # Method names appear in between, in sort_order.
        method_cols = labels[6:-1]
        # Inactive 'OldRetiredMethod' must NOT be a column.
        assert 'OldRetiredMethod' not in method_cols, (
            f"Inactive payment method must not appear as a column; "
            f"got {method_cols}")
        # All 3 active methods are present.
        for name in ('TestSNAP', 'TestCash', 'TestFMNP'):
            assert name in method_cols, (
                f"Active method {name!r} missing from headers: "
                f"{method_cols}")

    def test_eligible_vendor_shows_green_check(
            self, qtbot, matrix_db):
        """Vendor 1 (Apple Stand) accepts SNAP → cell shows ✓."""
        from fam.ui.settings_screen import SettingsScreen
        from fam.ui.styles import ACCENT_GREEN
        from PySide6.QtGui import QColor

        screen = SettingsScreen()
        qtbot.addWidget(screen)
        _seed_methods_in_screen(screen)
        labels = _header_labels(screen.vendors_table)
        snap_col = labels.index('TestSNAP')

        # Find Apple Stand's row.
        apple_row = None
        for r in range(screen.vendors_table.rowCount()):
            if screen.vendors_table.item(r, 1).text() == 'Apple Stand':
                apple_row = r
                break
        assert apple_row is not None

        cell = screen.vendors_table.item(apple_row, snap_col)
        assert cell.text() == '✓', (
            f"Apple Stand × TestSNAP should be ✓, got {cell.text()!r}")
        # Color matches the eligibility convention.
        assert cell.foreground().color() == QColor(ACCENT_GREEN), (
            f"Eligible cell should be ACCENT_GREEN")

    def test_ineligible_vendor_shows_red_x(self, qtbot, matrix_db):
        """Vendor 2 (Bakery) does NOT accept TestCash → cell ✗."""
        from fam.ui.settings_screen import SettingsScreen
        from fam.ui.styles import ERROR_COLOR
        from PySide6.QtGui import QColor

        screen = SettingsScreen()
        qtbot.addWidget(screen)
        _seed_methods_in_screen(screen)
        labels = _header_labels(screen.vendors_table)
        cash_col = labels.index('TestCash')

        bakery_row = None
        for r in range(screen.vendors_table.rowCount()):
            if screen.vendors_table.item(r, 1).text() == 'Bakery':
                bakery_row = r
                break
        assert bakery_row is not None

        cell = screen.vendors_table.item(bakery_row, cash_col)
        assert cell.text() == '✗', (
            f"Bakery × TestCash should be ✗, got {cell.text()!r}")
        assert cell.foreground().color() == QColor(ERROR_COLOR), (
            f"Ineligible cell should be ERROR_COLOR")

    def test_method_columns_sorted_by_sort_order(
            self, qtbot, matrix_db):
        """Columns appear in payment_method.sort_order."""
        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        _seed_methods_in_screen(screen)
        labels = _header_labels(screen.vendors_table)
        method_cols = labels[6:-1]
        # sort_order: TestSNAP=1, TestCash=2, TestFMNP=3.
        # (Plus any seed methods — strip those.)
        idx_test_snap = method_cols.index('TestSNAP')
        idx_test_cash = method_cols.index('TestCash')
        idx_test_fmnp = method_cols.index('TestFMNP')
        assert idx_test_snap < idx_test_cash < idx_test_fmnp, (
            f"Columns out of sort_order: {method_cols}")

    def test_methods_dialog_accept_refreshes_matrix(
            self, qtbot, matrix_db, monkeypatch):
        """After the per-vendor Methods dialog returns Accepted, the
        matrix must update WITHOUT requiring a tab switch."""
        from fam.ui.settings_screen import (
            SettingsScreen, VendorEligiblePaymentMethodsDialog,
        )
        from PySide6.QtWidgets import QDialog

        screen = SettingsScreen()
        qtbot.addWidget(screen)
        _seed_methods_in_screen(screen)
        labels = _header_labels(screen.vendors_table)
        cash_col = labels.index('TestCash')

        # Bakery currently does NOT accept TestCash.
        bakery_row = None
        for r in range(screen.vendors_table.rowCount()):
            if screen.vendors_table.item(r, 1).text() == 'Bakery':
                bakery_row = r
                break
        assert screen.vendors_table.item(
            bakery_row, cash_col).text() == '✗'

        # Stub the dialog to return Accepted with TestCash now CHECKED.
        def stub_init(self, vendor, parent=None):
            QDialog.__init__(self, parent)
            self.vendor = vendor
        def stub_exec(self):
            return QDialog.Accepted
        def stub_get_ids(self):
            # Bakery now accepts SNAP + Cash + FMNP (all 3).
            return {101, 102, 103}
        monkeypatch.setattr(
            VendorEligiblePaymentMethodsDialog, '__init__', stub_init)
        monkeypatch.setattr(
            VendorEligiblePaymentMethodsDialog, 'exec', stub_exec)
        monkeypatch.setattr(
            VendorEligiblePaymentMethodsDialog,
            'get_checked_payment_method_ids', stub_get_ids)

        # Trigger the dialog open → save → reload chain.
        screen._assign_payment_methods_to_vendor(2)

        # Re-read the cell — find Bakery's new row index (may have
        # changed if sorting was disabled-then-enabled).
        labels = _header_labels(screen.vendors_table)
        cash_col = labels.index('TestCash')
        bakery_row = None
        for r in range(screen.vendors_table.rowCount()):
            if screen.vendors_table.item(r, 1).text() == 'Bakery':
                bakery_row = r
                break
        assert screen.vendors_table.item(
            bakery_row, cash_col).text() == '✓', (
            "Matrix must refresh immediately after Methods dialog "
            "Accept — manager shouldn't have to switch tabs")

    def test_actions_column_remains_last(self, qtbot, matrix_db):
        """The Actions cell must still be the right-most column —
        the Edit/Markets/Methods/Deactivate buttons live there."""
        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        _seed_methods_in_screen(screen)
        last_col = screen.vendors_table.columnCount() - 1
        # Header text.
        assert screen.vendors_table.horizontalHeaderItem(
            last_col).text() == 'Actions'
        # Each row has a cellWidget (the action buttons row).
        for r in range(screen.vendors_table.rowCount()):
            assert screen.vendors_table.cellWidget(r, last_col) \
                is not None, (
                f"Row {r} missing Actions cellWidget at col "
                f"{last_col}")

    def test_method_header_tooltip_explains_legend(
            self, qtbot, matrix_db):
        """Each method-column header has a tooltip with match%,
        denomination (if any), and the ✓/✗ legend."""
        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        _seed_methods_in_screen(screen)
        labels = _header_labels(screen.vendors_table)
        snap_col = labels.index('TestSNAP')
        tip = screen.vendors_table.horizontalHeaderItem(
            snap_col).toolTip()
        assert 'TestSNAP' in tip
        assert '100' in tip  # match percent
        assert '✓' in tip and '✗' in tip, (
            "Header tooltip should explain the ✓/✗ legend")
