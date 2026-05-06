"""Tests for the v1.9.9 Adjustments-page date range filter.

Evolution of the filter
-----------------------
v1 — added a date filter that targeted ``md.date`` (the linked market
day's business date), mirroring the Reports screen.  Coordinators
flagged confusion because adjustments are entered DAYS after the
market closes, so the Created column timestamp didn't match the
filter window.

v2 (this module) — re-targeted the filter at ``last_updated`` (= the
most recent ``audit_log`` entry referencing the transaction, falling
back to ``created_at`` when none exists).  Matches the Adjustments-
page mental model: "show me transactions I worked on this week",
not "show me transactions belonging to this market week".  An
adjustment to a 6-month-old transaction made today now surfaces in
today's window.

A dedicated "Last Updated" column makes the filter target visible.
The "Created" column stays for forensics ("when was this first
entered?"), and the "Market Date" column stays for business context
("what market day's revenue does this belong to?").  Three dates
per row, each with a clear purpose.

Pinned dimensions
-----------------
  1. Model layer: ``last_updated`` is derived correctly (audit-log
     takes precedence over created_at) and the date filter operates
     on it inclusively.
  2. AdminScreen wires the widget, labels it "Last Updated", uses
     a tooltip that distinguishes from the Reports semantic, and
     re-runs the search live on range_changed.
"""

import inspect

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def seeded_db(tmp_path):
    """Three market days across two months, one transaction each.
    Explicit ``created_at`` values let the tests verify the
    ``last_updated``-based filter (which falls back to created_at
    when no audit entries exist for a transaction).

    Three transactions, all entered on the same date as their
    respective market day so the filter window semantics are
    intuitive in the test (TX-MAR entered 2026-03-15, etc.).  Tests
    that need to exercise the audit-log-takes-precedence path
    insert audit rows explicitly."""
    db_file = str(tmp_path / "adj_dates.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute("INSERT INTO markets (id, name) VALUES (1, 'M')")
    conn.execute("INSERT INTO vendors (id, name) VALUES (1, 'V')")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        " opened_by) VALUES "
        " (1, 1, '2026-03-15', 'Closed', 'T'),"
        " (2, 1, '2026-04-01', 'Closed', 'T'),"
        " (3, 1, '2026-04-29', 'Open',   'T')"
    )
    conn.execute(
        "INSERT INTO transactions (id, fam_transaction_id, "
        " market_day_id, vendor_id, receipt_total, status, "
        " created_at) VALUES"
        " (1, 'TX-MAR',   1, 1, 1000, 'Confirmed', '2026-03-15 10:00:00'),"
        " (2, 'TX-APR1',  2, 1, 2000, 'Confirmed', '2026-04-01 10:00:00'),"
        " (3, 'TX-APR29', 3, 1, 3000, 'Confirmed', '2026-04-29 10:00:00')"
    )
    conn.commit()
    yield conn
    close_connection()


# ══════════════════════════════════════════════════════════════════
# 1. Model layer
# ══════════════════════════════════════════════════════════════════
class TestSearchTransactionsDateFilter:

    def test_no_filter_returns_all(self, seeded_db):
        from fam.models.transaction import search_transactions
        rows = search_transactions()
        assert len(rows) == 3

    def test_date_from_only_filters_lower_bound(self, seeded_db):
        from fam.models.transaction import search_transactions
        rows = search_transactions(date_from='2026-04-01')
        ids = {r['fam_transaction_id'] for r in rows}
        assert ids == {'TX-APR1', 'TX-APR29'}, (
            "date_from must be an INCLUSIVE lower bound — the "
            "April 1 row must be returned when date_from=April 1.")

    def test_date_to_only_filters_upper_bound(self, seeded_db):
        from fam.models.transaction import search_transactions
        rows = search_transactions(date_to='2026-04-01')
        ids = {r['fam_transaction_id'] for r in rows}
        assert ids == {'TX-MAR', 'TX-APR1'}, (
            "date_to must be an INCLUSIVE upper bound.")

    def test_both_endpoints_apply_a_window(self, seeded_db):
        from fam.models.transaction import search_transactions
        rows = search_transactions(
            date_from='2026-04-01', date_to='2026-04-15')
        ids = {r['fam_transaction_id'] for r in rows}
        assert ids == {'TX-APR1'}

    def test_empty_window_returns_no_rows(self, seeded_db):
        from fam.models.transaction import search_transactions
        rows = search_transactions(
            date_from='2025-01-01', date_to='2025-12-31')
        assert rows == []

    def test_combines_with_status_filter(self, seeded_db):
        """Date range stacks with the existing filters — pin that
        a future refactor doesn't accidentally short-circuit one."""
        from fam.models.transaction import search_transactions
        # All 3 transactions are 'Confirmed'.  Restricting status
        # to a non-existent value with a wide date range must
        # return zero rows (proves both filters are AND'd).
        rows = search_transactions(
            date_from='2026-01-01', date_to='2026-12-31',
            status='Voided',
        )
        assert rows == []
        # And the positive case still hits exactly 1.
        rows = search_transactions(
            date_from='2026-04-01', date_to='2026-04-15',
            status='Confirmed',
        )
        assert len(rows) == 1

    def test_last_updated_field_present_in_results(self, seeded_db):
        """Every returned row must carry the derived
        ``last_updated`` field so the AdminScreen can populate the
        new Last Updated column without falling back to created_at."""
        from fam.models.transaction import search_transactions
        rows = search_transactions()
        assert rows
        for r in rows:
            assert 'last_updated' in r, (
                f"Row {r.get('fam_transaction_id')!r} missing "
                f"'last_updated' field")
            # No audits in the fixture → falls back to created_at.
            assert r['last_updated'] == r['created_at']

    def test_audit_log_entry_takes_precedence_over_created_at(
            self, seeded_db):
        """When a transaction has audit_log entries, the most recent
        one's ``changed_at`` becomes ``last_updated`` — and the
        date filter sees the new (later) date, not the original
        created_at.

        Concrete: TX-MAR was created 2026-03-15.  An adjustment
        audit entry on 2026-04-29 should make TX-MAR appear in a
        2026-04-29 date filter, NOT in a 2026-03-15 filter."""
        from fam.models.transaction import search_transactions
        conn = get_connection()
        # Synthesize an ADJUST audit entry on 2026-04-29 for TX-MAR.
        conn.execute(
            "INSERT INTO audit_log (table_name, record_id, action, "
            " field_name, old_value, new_value, changed_by, "
            " changed_at) VALUES "
            " ('transactions', 1, 'ADJUST', 'receipt_total', "
            "  '1000', '1500', 'Tester', '2026-04-29 14:00:00')"
        )
        conn.commit()

        # March 15 window: should NOT include TX-MAR anymore (its
        # last_updated jumped to April 29).
        rows = search_transactions(
            date_from='2026-03-15', date_to='2026-03-15')
        ids = {r['fam_transaction_id'] for r in rows}
        assert 'TX-MAR' not in ids, (
            "TX-MAR was adjusted on April 29 — its last_updated "
            "must reflect that; March 15 filter should now miss it.")

        # April 29 window: SHOULD include TX-MAR (newly updated)
        # alongside TX-APR29 (created that day).
        rows = search_transactions(
            date_from='2026-04-29', date_to='2026-04-29')
        ids = {r['fam_transaction_id'] for r in rows}
        assert 'TX-MAR' in ids, (
            "An April 29 adjustment of TX-MAR must surface in "
            "April 29's filter window.  This is the entire point "
            "of the v2 last_updated-based filter.")
        assert 'TX-APR29' in ids

    def test_payment_line_items_audit_also_counts_for_last_updated(
            self, seeded_db):
        """The audit log records ``payment_line_items`` changes
        with the transaction's id as record_id.  Those entries
        must contribute to last_updated too — otherwise a payment
        adjustment (which is the most common kind) wouldn't shift
        the transaction into the activity window."""
        from fam.models.transaction import search_transactions
        conn = get_connection()
        # PAYMENT_ADJUSTED is logged against table_name=
        # 'payment_line_items' with record_id = txn id.
        conn.execute(
            "INSERT INTO audit_log (table_name, record_id, action, "
            " changed_by, changed_at) VALUES "
            " ('payment_line_items', 2, 'PAYMENT_ADJUSTED', "
            "  'Tester', '2026-05-10 09:00:00')"
        )
        conn.commit()
        rows = search_transactions(
            date_from='2026-05-10', date_to='2026-05-10')
        ids = {r['fam_transaction_id'] for r in rows}
        assert ids == {'TX-APR1'}, (
            "Payment-line-item audits must count toward "
            "last_updated — they ARE the transaction's activity.")


# ══════════════════════════════════════════════════════════════════
# 2. AdminScreen wiring (source-level)
# ══════════════════════════════════════════════════════════════════
class TestAdminScreenDateRangeWidget:

    def test_admin_screen_imports_date_range_widget(self):
        import fam.ui.admin_screen as ams
        src = inspect.getsource(ams)
        assert 'DateRangeWidget' in src, (
            "AdminScreen must import DateRangeWidget from helpers "
            "to provide date-range filtering parity with Reports.")

    def test_filter_bar_constructs_date_range(self):
        from fam.ui.admin_screen import AdminScreen
        src = inspect.getsource(AdminScreen._build_ui)
        assert 'self.date_range = DateRangeWidget()' in src, (
            "_build_ui must instantiate ``self.date_range`` so the "
            "rest of the screen can reach it.")
        assert 'self.date_range.range_changed.connect' in src, (
            "The widget's range_changed signal must trigger a "
            "search so the filter feels live (like Reports).")

    def test_search_passes_date_range_to_model(self):
        from fam.ui.admin_screen import AdminScreen
        src = inspect.getsource(AdminScreen._search)
        assert 'self.date_range.get_date_range()' in src
        assert 'date_from=' in src and 'date_to=' in src, (
            "_search must thread date_from / date_to to "
            "search_transactions — without these kwargs the SQL "
            "filter never gets applied.")

    def test_load_market_days_pushes_bounds_to_widget(self):
        """The date-range picker must have its spinbox bounds set
        to the actual data window so it can't roam outside.  v2
        derives the bounds from activity dates (transactions +
        audit_log) rather than the market_days table — see
        ``TestDateFilterUXClarity.test_load_market_days_bounds_use_activity_dates``
        for the source-level guard on that derivation."""
        from fam.ui.admin_screen import AdminScreen
        src = inspect.getsource(AdminScreen._load_market_days)
        assert 'set_date_bounds' in src


# ══════════════════════════════════════════════════════════════════
# 3. UX clarity — filter labelling + Last Updated column
# ══════════════════════════════════════════════════════════════════
class TestDateFilterUXClarity:
    """v2 of the filter targets ``last_updated`` (most recent audit
    activity, falling back to created_at).  The label must reflect
    that, the tooltip must disambiguate from the Reports filter
    semantic, and the table must show a "Last Updated" column so
    coordinators can verify the filter window matches the rows."""

    def test_filter_label_is_last_updated(self):
        from fam.ui.admin_screen import AdminScreen
        src = inspect.getsource(AdminScreen._build_ui)
        assert 'make_field_label("Last Updated")' in src, (
            "Filter label must be 'Last Updated' to match what the "
            "filter actually targets.  Earlier labels ('Dates', "
            "'Market Date') misled coordinators into expecting "
            "different filter semantics.")

    def test_filter_has_disambiguating_tooltip(self):
        from fam.ui.admin_screen import AdminScreen
        src = inspect.getsource(AdminScreen._build_ui)
        assert 'self.date_range.setToolTip' in src
        # The tooltip must explicitly distinguish from the Reports
        # screen filter — different mental model, different target.
        assert 'Reports' in src, (
            "Tooltip must call out that this filter is "
            "intentionally different from the Reports screen's "
            "date filter (Reports = market day, Adjustments = "
            "activity date).  Without this, coordinators jumping "
            "between screens hit unexplained behavior changes.")

    def test_table_has_last_updated_column(self):
        from fam.ui.admin_screen import AdminScreen
        src = inspect.getsource(AdminScreen._build_ui)
        assert 'setColumnCount(10)' in src, (
            "Adjustments table must have 10 columns: Transaction "
            "ID, Customer ID, Market, Market Date, Vendor, Receipt "
            "Total, Status, Created, Last Updated, Actions.")
        assert '"Last Updated"' in src
        # Market Date stays for business-context reference.
        assert '"Market Date"' in src
        assert '"Created"' in src

    def test_actions_column_index_moved_to_nine(self):
        """When Last Updated was inserted at index 8, the Actions
        column shifted to index 9.  Pin both wiring sites
        (``configure_table`` + ``setCellWidget``) so off-by-one
        bugs surface immediately."""
        from fam.ui.admin_screen import AdminScreen
        build_src = inspect.getsource(AdminScreen._build_ui)
        search_src = inspect.getsource(AdminScreen._search)
        assert 'actions_col=9' in build_src
        assert 'setCellWidget(i, 9, action_widget)' in search_src

    def test_search_populates_last_updated_from_derived_field(self):
        """The Last Updated cell must read the ``last_updated``
        field that ``search_transactions`` derives via the
        audit-log subquery — NOT a hand-rolled fallback to
        created_at, which would miss adjustments entirely."""
        from fam.ui.admin_screen import AdminScreen
        src = inspect.getsource(AdminScreen._search)
        # Pin via the field name + fallback expression.
        assert "t.get('last_updated', '')" in src, (
            "Column 8 must read the derived last_updated field — "
            "anything else (e.g. defaulting to created_at) hides "
            "the audit activity the filter targets.")

    def test_load_market_days_bounds_use_activity_dates(self):
        """The picker bounds should reflect the activity-date span
        (transactions.created_at + audit_log.changed_at), not just
        the market_days.date range — otherwise the popup spinboxes
        won't include dates where adjustments were made for older
        market days."""
        from fam.ui.admin_screen import AdminScreen
        src = inspect.getsource(AdminScreen._load_market_days)
        assert 'audit_log' in src, (
            "Picker bounds must be derived from the audit_log + "
            "transactions union so the spinbox range covers every "
            "day where activity actually happened.")
        assert 'set_date_bounds' in src
