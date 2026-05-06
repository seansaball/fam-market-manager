"""Tests for the v1.9.9 Unallocated Funds + Adjustments customer-gone path.

Background
----------
The Adjustments page is where market managers reconcile transactions
*after the fact* against vendor receipts.  When the reconciliation
finds the customer was undercharged, the customer is usually no longer
on site to pay — markets close hours before reconciliation happens.

Before v1.9.9, the dialog either silently saved a fictional "customer
paid more" state or blocked save with a "Payment Mismatch" error and
no clean recovery path.  Either way, FAM's books quietly absorbed the
loss with zero accounting trail.

v1.9.9 adds a first-class category — *Unallocated Funds* — modeled as
a system payment method (``is_system=1``) so it flows through every
existing per-method aggregate (Vendor Reimbursement column, FAM Match
Report column, Detailed Ledger row) for free.  When the manager
confirms via the new popup that the customer is gone, the dialog
auto-injects an Unallocated Funds line item for the gap, and the
audit log gets a dedicated ``UNALLOCATED_FUNDS`` action with the
``unallocated_funds`` reason_code auto-set.

This module covers:
  1. Schema v24→v25 (column + seed + permissive vendor backfill)
  2. Model helpers (``get_unallocated_funds_method``, filter flags)
  3. Source-level guards on the Adjustments popup + audit wiring
  4. Source-level guards that selection UIs hide the system method
  5. Reports: ``FAM Absorbed`` field on FAM Match collector + UI
  6. Settings: system methods are locked
"""

import inspect

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import (
    initialize_database,
    CURRENT_SCHEMA_VERSION,
    _migrate_v24_to_v25,
)


# ══════════════════════════════════════════════════════════════════
# Fixture: fresh DB per test
# ══════════════════════════════════════════════════════════════════
@pytest.fixture
def fresh_db(tmp_path):
    db_file = str(tmp_path / "uf_test.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield tmp_path, db_file
    close_connection()


# ══════════════════════════════════════════════════════════════════
# 1. Schema — column, seed, vendor backfill
# ══════════════════════════════════════════════════════════════════
class TestSchemaV25:

    def test_schema_version_is_at_least_25(self):
        # is_system / Unallocated Funds were introduced at v25.
        # The schema may have moved past v25 for unrelated features;
        # this test pins the lower bound.
        assert CURRENT_SCHEMA_VERSION >= 25, (
            "Schema must be at least v25 for the Unallocated Funds "
            "system payment method.")

    def test_payment_methods_has_is_system_column(self, fresh_db):
        conn = get_connection()
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(payment_methods)").fetchall()}
        assert 'is_system' in cols

    def test_unallocated_funds_seeded_on_fresh_install(self, fresh_db):
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM payment_methods WHERE name = ?",
            ('Unallocated Funds',)
        ).fetchone()
        assert row is not None, (
            "Fresh install must seed Unallocated Funds — the "
            "Adjustments customer-gone path needs the row to exist "
            "from day one, not only after an upgrade migration.")
        d = dict(row)
        assert d['is_system'] == 1
        assert d['match_percent'] == 0.0
        assert d['is_active'] == 1
        # Denomination must be NULL — the gap is an arbitrary dollar
        # amount; locking it to a fixed denomination would defeat
        # the purpose.
        assert d['denomination'] is None

    def test_migration_is_idempotent(self, fresh_db):
        """Running v24→v25 a second time on an already-migrated DB
        should not error and should not duplicate the seed row."""
        conn = get_connection()
        before = conn.execute(
            "SELECT COUNT(*) FROM payment_methods WHERE name = ?",
            ('Unallocated Funds',)
        ).fetchone()[0]
        _migrate_v24_to_v25(conn)
        after = conn.execute(
            "SELECT COUNT(*) FROM payment_methods WHERE name = ?",
            ('Unallocated Funds',)
        ).fetchone()[0]
        assert before == 1 and after == 1, (
            "Re-running the migration must not duplicate the seed; "
            "the INSERT uses OR IGNORE for exactly this case.")

    def test_vendor_backfill_makes_unallocated_eligible_everywhere(
            self, fresh_db):
        """Every existing vendor should be eligible for Unallocated
        Funds out of the box.  The Adjustments customer-gone path
        cannot fail vendor-eligibility validation — that would
        defeat the entire recovery mechanism."""
        conn = get_connection()
        # Insert two vendors before re-running the migration to
        # simulate an upgrade scenario.
        conn.execute("INSERT INTO vendors (name) VALUES ('Vendor A')")
        conn.execute("INSERT INTO vendors (name) VALUES ('Vendor B')")
        conn.commit()
        # Clear any prior backfill rows + re-run the migration as if
        # we were upgrading a DB that just got the two vendors.
        conn.execute(
            "DELETE FROM vendor_payment_methods "
            "WHERE payment_method_id = "
            "(SELECT id FROM payment_methods WHERE name = ?)",
            ('Unallocated Funds',))
        conn.commit()
        _migrate_v24_to_v25(conn)

        eligible = conn.execute("""
            SELECT v.name FROM vendors v
            JOIN vendor_payment_methods vpm ON vpm.vendor_id = v.id
            JOIN payment_methods pm ON vpm.payment_method_id = pm.id
            WHERE pm.name = 'Unallocated Funds'
            ORDER BY v.name
        """).fetchall()
        eligible_names = [r[0] for r in eligible]
        assert 'Vendor A' in eligible_names
        assert 'Vendor B' in eligible_names


# ══════════════════════════════════════════════════════════════════
# 2. Model helpers
# ══════════════════════════════════════════════════════════════════
class TestPaymentMethodHelpers:

    def test_unallocated_funds_constant_matches_seed(self):
        from fam.models.payment_method import UNALLOCATED_FUNDS_NAME
        assert UNALLOCATED_FUNDS_NAME == 'Unallocated Funds'

    def test_get_unallocated_funds_method_returns_seed(self, fresh_db):
        from fam.models.payment_method import get_unallocated_funds_method
        row = get_unallocated_funds_method()
        assert row is not None
        assert row['name'] == 'Unallocated Funds'
        assert row['is_system'] == 1

    def test_is_system_method_handles_missing_key(self):
        """Pre-v25 schemas don't have is_system; rows queried then
        must surface as NOT system rather than crashing."""
        from fam.models.payment_method import is_system_method
        assert is_system_method({'name': 'Cash'}) is False
        assert is_system_method({'name': 'Cash', 'is_system': 0}) is False
        assert is_system_method({'name': 'X', 'is_system': 1}) is True
        assert is_system_method(None) is False

    def test_include_system_false_hides_unallocated_funds(self, fresh_db):
        from fam.models.payment_method import get_all_payment_methods
        with_sys = get_all_payment_methods(include_system=True)
        without_sys = get_all_payment_methods(include_system=False)
        with_names = {m['name'] for m in with_sys}
        without_names = {m['name'] for m in without_sys}
        assert 'Unallocated Funds' in with_names
        assert 'Unallocated Funds' not in without_names, (
            "include_system=False is what selection dropdowns pass "
            "to keep coordinators from picking Unallocated Funds "
            "manually — it must filter the seed row out.")

    def test_market_query_with_include_system_false(self, fresh_db):
        """Same filter must work via the market-scoped helper, since
        the Payment screen calls it preferentially."""
        from fam.models.payment_method import (
            get_payment_methods_for_market, assign_payment_method_to_market,
            get_unallocated_funds_method,
        )
        conn = get_connection()
        conn.execute("INSERT INTO markets (name) VALUES ('Test Mkt')")
        conn.commit()
        market_id = conn.execute(
            "SELECT id FROM markets WHERE name = 'Test Mkt'"
        ).fetchone()[0]
        # Wire Unallocated Funds + a regular method to this market
        conn.execute("INSERT INTO payment_methods (name, match_percent) "
                     "VALUES ('Cash', 0)")
        conn.commit()
        cash_id = conn.execute(
            "SELECT id FROM payment_methods WHERE name = 'Cash'"
        ).fetchone()[0]
        uf_id = get_unallocated_funds_method()['id']
        assign_payment_method_to_market(market_id, cash_id)
        assign_payment_method_to_market(market_id, uf_id)

        with_sys = {m['name'] for m in get_payment_methods_for_market(
            market_id, include_system=True)}
        without_sys = {m['name'] for m in get_payment_methods_for_market(
            market_id, include_system=False)}
        assert 'Unallocated Funds' in with_sys
        assert 'Unallocated Funds' not in without_sys
        assert 'Cash' in without_sys


# ══════════════════════════════════════════════════════════════════
# 3. Adjustments dialog source-level guards
# ══════════════════════════════════════════════════════════════════
class TestAdjustmentsCustomerGonePath:
    """The popup + auto-inject behaviour lives in
    ``AdminScreen._adjust_transaction``.  The actual flow is hard to
    exercise without instantiating Qt widgets and a full dialog, so
    these tests pin the *contract* at the source level — the right
    function calls happen in the right order, and the right action
    code + reason_code feed the audit log."""

    def _src(self):
        from fam.ui.admin_screen import AdminScreen
        return inspect.getsource(AdminScreen._adjust_transaction)

    def test_gap_is_computed_against_receipt_total(self):
        src = self._src()
        # gap = new_total_cents - allocated  is the load-bearing
        # expression that decides whether to show the popup.
        assert 'gap = new_total_cents - allocated' in src, (
            "The gap detection MUST use new_total - allocated so the "
            "popup fires whenever the customer would owe more, not "
            "only when the receipt total itself increased.")

    def test_popup_only_fires_on_positive_gap(self):
        """Refunds (negative gap) intentionally bypass the popup —
        the existing reconciliation-error path is correct for those."""
        src = self._src()
        assert 'if gap > 1' in src, (
            "The trigger condition must be a positive gap.  A "
            "literal `gap != 0` or `abs(gap) > 1` would also fire "
            "for refunds, which would surface a misleading "
            "'customer must pay' prompt for the opposite scenario.")

    def test_uses_get_unallocated_funds_method_lookup(self):
        """The customer-gone path must use the named lookup so any
        future rename of the seed row only needs a single edit.
        v1.9.9 refactor moved the lookup into a module-level
        helper (``_append_unallocated_funds_row``) — the test now
        verifies the helper exists, uses the named lookup, and is
        called from ``_adjust_transaction``."""
        import fam.ui.admin_screen as ams
        helper_src = inspect.getsource(ams._append_unallocated_funds_row)
        assert 'get_unallocated_funds_method' in helper_src, (
            "_append_unallocated_funds_row helper must use the "
            "named lookup so any future rename of the seed row "
            "only needs a single code edit.")
        # And _adjust_transaction must use the helper.
        assert '_append_unallocated_funds_row' in self._src()

    def test_injected_row_has_zero_customer_charged_and_match(self):
        """The whole point of Unallocated Funds: customer paid $0,
        FAM didn't 'match' anything — it absorbed the whole gap.
        Refactored to live in ``_append_unallocated_funds_row``;
        pin the literal there."""
        import fam.ui.admin_screen as ams
        helper_src = inspect.getsource(ams._append_unallocated_funds_row)
        assert "'customer_charged': 0" in helper_src
        assert "'match_amount': 0" in helper_src
        # method_amount is parameterised on the helper signature,
        # not a literal.  Confirm via the parameter name.
        assert "'method_amount': method_amount_cents" in helper_src

    def test_writes_dedicated_audit_action(self):
        src = self._src()
        assert "'UNALLOCATED_FUNDS'" in src, (
            "The audit log must distinguish Unallocated Funds from "
            "regular PAYMENT_ADJUSTED so the Activity Log surfaces "
            "FAM-absorbed losses as their own story.")
        assert "reason_code='unallocated_funds'" in src, (
            "Per the design Q&A, reason_code is auto-set (not "
            "shown in the dropdown).  Pin the literal so a refactor "
            "can't silently drop it.")

    def test_force_payments_did_change_when_unallocated_injected(self):
        """If the user only changed the receipt total and didn't
        touch payment rows, ``dialog.payments_changed()`` would
        return False — but we just appended an Unallocated Funds
        row to ``new_items`` that needs to be saved.  The OR clause
        forces the save through."""
        src = self._src()
        assert 'unallocated_funds_cents > 0' in src, (
            "payments_did_change must be forced True when the "
            "customer-gone path injected a row, otherwise "
            "save_payment_line_items is skipped and the gap stays "
            "open in the DB despite the manager's confirmation.")

    def test_post_save_message_avoids_telling_to_charge_customer(self):
        """The default 'collect more from the customer' impact
        message would be the exact opposite of the intent the
        popup just confirmed.  A tailored Unallocated Funds
        message must be shown instead."""
        src = self._src()
        assert 'Unallocated Funds Logged' in src, (
            "When unallocated_funds_cents > 0 the post-save "
            "QMessageBox must show the Unallocated Funds title, "
            "NOT 'Customer Impact' (which says 'collect more from "
            "the customer' — wrong when they're gone).")


# ══════════════════════════════════════════════════════════════════
# 4. Selection-UI source-level guards
# ══════════════════════════════════════════════════════════════════
class TestSelectionUIsHideSystemMethods:
    """Coordinators must not be able to manually pick Unallocated
    Funds from any normal entry dropdown — it's only auto-injected
    by the Adjustments customer-gone path.  Pin ``include_system=
    False`` at the selection-dropdown call sites."""

    def test_payment_row_dropdown_filters_system(self):
        import fam.ui.widgets.payment_row as pr
        src = inspect.getsource(pr)
        # The combo-load function must use include_system=False.
        assert 'include_system=False' in src, (
            "PaymentRow's method dropdown is the central choke "
            "point for both the Payment screen and the Adjustments "
            "dialog — without this filter, Unallocated Funds would "
            "appear as a manual option.")

    def test_payment_screen_breakdown_filters_system(self):
        import fam.ui.payment_screen as ps
        src = inspect.getsource(ps)
        assert 'include_system=False' in src, (
            "Payment screen vendor-breakdown columns + auto-"
            "distribute candidate selection must skip system "
            "methods.")

    def test_admin_screen_method_choices_filters_system(self):
        import fam.ui.admin_screen as adm
        src = inspect.getsource(adm)
        assert 'include_system=False' in src, (
            "Adjustments dialog's _refresh_method_choices must "
            "exclude system methods so they don't count toward "
            "the 'next method to add' availability check.")


# ══════════════════════════════════════════════════════════════════
# 5. Reports — FAM Absorbed surfaces in collector + UI
# ══════════════════════════════════════════════════════════════════
class TestFAMMatchReportSurfacesAbsorbed:

    def test_data_collector_includes_fam_absorbed_field(self, fresh_db):
        """Every row in the synced FAM Match Report must carry the
        ``FAM Absorbed`` key so the Google Sheets tab has uniform
        column shape regardless of whether any losses occurred."""
        from fam.sync.data_collector import _collect_fam_match
        conn = get_connection()

        # Build a minimal market day with one Unallocated Funds line.
        conn.execute("INSERT INTO markets (name) VALUES ('M')")
        conn.execute(
            "INSERT INTO vendors (name) VALUES ('V')")
        conn.execute(
            "INSERT INTO market_days (market_id, date, status, "
            "opened_by) VALUES (1, '2026-04-29', 'Closed', 'Tester')")
        conn.execute(
            "INSERT INTO transactions (fam_transaction_id, "
            " market_day_id, vendor_id, receipt_total, status) "
            "VALUES ('TX1', 1, 1, 5000, 'Adjusted')")
        # One real Cash row + one Unallocated Funds row
        conn.execute("INSERT INTO payment_methods (name, match_percent) "
                     "VALUES ('Cash', 0.0)")
        cash_id = conn.execute(
            "SELECT id FROM payment_methods WHERE name = 'Cash'"
        ).fetchone()[0]
        uf_id = conn.execute(
            "SELECT id FROM payment_methods WHERE name = 'Unallocated Funds'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO payment_line_items (transaction_id, "
            " payment_method_id, method_name_snapshot, "
            " match_percent_snapshot, method_amount, match_amount, "
            " customer_charged) VALUES (1, ?, 'Cash', 0, 4000, 0, 4000)",
            (cash_id,))
        conn.execute(
            "INSERT INTO payment_line_items (transaction_id, "
            " payment_method_id, method_name_snapshot, "
            " match_percent_snapshot, method_amount, match_amount, "
            " customer_charged) VALUES (1, ?, 'Unallocated Funds', "
            " 0, 1000, 0, 0)",
            (uf_id,))
        conn.commit()

        rows = _collect_fam_match(conn, md_id=1)
        # Every row gets the column
        for r in rows:
            assert 'FAM Absorbed' in r, (
                f"Every row must carry 'FAM Absorbed' for stable "
                f"sheet shape; row {r['Payment Method']!r} is "
                f"missing it.")

        by_method = {r['Payment Method']: r for r in rows}
        # Cash row: FAM Absorbed must be 0.
        assert by_method['Cash']['FAM Absorbed'] == 0
        # Unallocated Funds row: FAM Absorbed = method_amount ($10).
        assert by_method['Unallocated Funds']['FAM Absorbed'] == 10.0
        # And the existing Total Allocated stays as method_amount.
        assert by_method['Unallocated Funds']['Total Allocated'] == 10.0
        # Match must be zero — this is absorption, not a match.
        assert by_method['Unallocated Funds']['Total FAM Match'] == 0

    def test_reports_screen_has_fam_absorbed_column(self):
        """Source-level guard on the Reports UI: 4 columns and the
        'FAM Absorbed' header must be present so the in-app FAM
        Match Report mirrors the synced one."""
        import fam.ui.reports_screen as rs
        src = inspect.getsource(rs)
        assert 'setColumnCount(4)' in src
        assert '"FAM Absorbed"' in src
        # And the totals card.
        assert 'fam_absorbed' in src, (
            "A 'FAM Absorbed' summary card must be wired so "
            "managers see the total at a glance — consistent with "
            "how 'FAM Match' and 'FMNP Checks' are surfaced.")


# ══════════════════════════════════════════════════════════════════
# 6. Settings UI — system methods are locked
# ══════════════════════════════════════════════════════════════════
class TestSettingsLocksSystemMethods:

    def test_system_methods_have_disabled_action_buttons(self):
        """Source-level guard: when ``is_system`` is true, the Edit/
        Up/Down/Toggle buttons must call ``setEnabled(False)``.
        Belt-and-suspenders against a coordinator renaming or
        deactivating Unallocated Funds and silently breaking the
        Adjustments customer-gone code path."""
        import fam.ui.settings_screen as ss
        src = inspect.getsource(ss._SettingsScreen._load_payment_methods
                                if hasattr(ss, '_SettingsScreen')
                                else ss.SettingsScreen._load_payment_methods)
        assert 'is_system' in src
        # Each of the four buttons gets the disable treatment.
        # Pin via simple count rather than identifying each one
        # individually so a future re-ordering of buttons doesn't
        # break the test for the wrong reason.
        assert src.count('setEnabled(False)') >= 4, (
            "All four action buttons (Edit / Up / Down / Toggle) "
            "must be disabled when the row is a system method.  "
            f"Found {src.count('setEnabled(False)')} disable calls; "
            "expected at least 4.")
