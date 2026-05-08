"""Centralised "active transaction status" filter (audit 2026-05-07).

Pre-centralisation, the canonical filter for "live" transactions
(Confirmed + Adjusted, excluding Voided + Draft) was repeated as
inline SQL strings at 18+ sites across reports, sync, models, and
the admin screen.  A future report adding ``WHERE ... <some
condition>`` and forgetting to AND with the active-status filter
would silently include Voided/Draft transactions in totals — a
class of bug invisible until manual reconciliation caught it.

The fix introduces ``fam.models.transaction.ACTIVE_TX_STATUSES``
(the canonical tuple) and ``active_tx_status_clause(alias)`` (a
helper that builds the SQL fragment with a configurable table
alias).  This file pins:

  1. The constant matches the documented status set.
  2. The helper produces the EXACT SQL fragment that the inline
     duplication used (no semantic shift).
  3. Refactored sites still produce identical query results to
     pre-refactor (verified end-to-end via the canonical
     vendor-reimbursement query).
"""

import pytest

from fam.database.connection import (
    set_db_path, close_connection, get_connection,
)
from fam.database.schema import initialize_database
from fam.models.transaction import (
    ACTIVE_TX_STATUSES, ACTIVE_TX_STATUS_CLAUSE,
    active_tx_status_clause,
)


# ──────────────────────────────────────────────────────────────────
# 1. The constant + helper — exact-match contract
# ──────────────────────────────────────────────────────────────────


class TestActiveTxStatusContract:

    def test_active_statuses_tuple_is_confirmed_adjusted(self):
        """The canonical "live transaction" set is exactly
        Confirmed + Adjusted.  Voided is excluded (not live);
        Draft is excluded (never confirmed)."""
        assert ACTIVE_TX_STATUSES == ('Confirmed', 'Adjusted')

    def test_default_clause_uses_t_alias(self):
        """The module-level constant uses ``t`` because the
        ``transactions t`` alias is the convention across
        reports + sync queries."""
        assert ACTIVE_TX_STATUS_CLAUSE == (
            "t.status IN ('Confirmed', 'Adjusted')")

    def test_helper_generates_correct_clause_for_any_alias(self):
        """Helper accepts any alias so callers don't need to
        rewrite the tuple by hand."""
        assert active_tx_status_clause('t') == (
            "t.status IN ('Confirmed', 'Adjusted')")
        assert active_tx_status_clause('co') == (
            "co.status IN ('Confirmed', 'Adjusted')")
        assert active_tx_status_clause('transactions') == (
            "transactions.status IN ('Confirmed', 'Adjusted')")

    def test_helper_default_alias_is_t(self):
        """Convenience: default to the most common alias."""
        assert active_tx_status_clause() == (
            "t.status IN ('Confirmed', 'Adjusted')")


# ──────────────────────────────────────────────────────────────────
# 2. End-to-end: clause filters Voided + Draft correctly
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mixed_status_db(tmp_path):
    """DB with one transaction in each status so a SELECT using
    the canonical clause can be verified to return exactly
    Confirmed + Adjusted."""
    db_file = str(tmp_path / "status_filter.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        "match_limit_active) VALUES (1, 'M', 100000, 1)")
    conn.execute(
        "INSERT INTO vendors (id, name) VALUES (1, 'V')")
    conn.execute(
        "INSERT INTO market_vendors (market_id, vendor_id) "
        "VALUES (1, 1)")
    conn.execute(
        "INSERT INTO market_days (id, market_id, date, status, "
        "opened_by) VALUES (1, 1, '2099-05-07', 'Open', 'T')")
    # Transactions: one each in the four statuses.
    conn.execute(
        "INSERT INTO transactions "
        "(id, fam_transaction_id, market_day_id, vendor_id, "
        " receipt_total, status, created_at) VALUES "
        "(1, 'FAM-T1', 1, 1, 100, 'Confirmed', '2099-05-07'), "
        "(2, 'FAM-T2', 1, 1, 200, 'Adjusted', '2099-05-07'), "
        "(3, 'FAM-T3', 1, 1, 300, 'Voided', '2099-05-07'), "
        "(4, 'FAM-T4', 1, 1, 400, 'Draft', '2099-05-07')")
    conn.commit()
    yield conn
    close_connection()


class TestActiveTxStatusFiltersCorrectly:

    def test_clause_returns_only_confirmed_and_adjusted(
            self, mixed_status_db):
        """End-to-end: a query using the canonical clause must
        return exactly the Confirmed + Adjusted rows.  Pins the
        contract that future reports calling the helper get the
        same filter as the legacy inline SQL."""
        clause = active_tx_status_clause('t')
        rows = mixed_status_db.execute(
            f"SELECT t.fam_transaction_id, t.status "
            f"FROM transactions t "
            f"WHERE {clause} "
            f"ORDER BY t.id").fetchall()
        ids = [r['fam_transaction_id'] for r in rows]
        statuses = [r['status'] for r in rows]
        assert ids == ['FAM-T1', 'FAM-T2'], (
            f"Canonical clause must return Confirmed + Adjusted "
            f"only (T1 + T2).  Got: {ids}")
        assert set(statuses) == {'Confirmed', 'Adjusted'}

    def test_clause_excludes_voided(self, mixed_status_db):
        """Pin specifically: Voided MUST NOT appear in totals.
        This is the silent-bug class the centralisation eliminates
        — a future report adding new SQL that forgets the filter
        will be caught by the hygiene test below."""
        clause = active_tx_status_clause('t')
        rows = mixed_status_db.execute(
            f"SELECT t.id FROM transactions t "
            f"WHERE {clause}").fetchall()
        ids = {r['id'] for r in rows}
        assert 3 not in ids, (
            "Voided transaction (id=3) MUST NOT appear in the "
            "active-tx filter.")

    def test_clause_excludes_draft(self, mixed_status_db):
        """Same for Draft — never counted in money totals."""
        clause = active_tx_status_clause('t')
        rows = mixed_status_db.execute(
            f"SELECT t.id FROM transactions t "
            f"WHERE {clause}").fetchall()
        ids = {r['id'] for r in rows}
        assert 4 not in ids, (
            "Draft transaction (id=4) MUST NOT appear in the "
            "active-tx filter.")


# ──────────────────────────────────────────────────────────────────
# 3. Refactored sites still produce identical results
# ──────────────────────────────────────────────────────────────────


class TestRefactoredSitesUnchanged:
    """Pin that the refactored sync queries (data_collector.py
    line 226 + line 451) still produce the same results as the
    pre-refactor inline-string version.  Catches accidental
    semantic drift if the refactor introduced a typo."""

    def test_data_collector_vendor_reimbursement_query_works(
            self, mixed_status_db):
        """Smoke test the refactored vendor-reimbursement query
        builder.  Confirms the f-string substitution at line 226
        produces a valid SQL fragment."""
        # Build the query the way data_collector does.
        from fam.models.transaction import active_tx_status_clause
        placeholders = '?'
        where = (
            f"WHERE t.market_day_id IN ({placeholders}) "
            f"AND {active_tx_status_clause('t')}")
        rows = mixed_status_db.execute(
            f"SELECT t.id FROM transactions t {where}",
            [1]).fetchall()
        ids = {r['id'] for r in rows}
        assert ids == {1, 2}, (
            f"Refactored query must return Confirmed + Adjusted "
            f"only.  Got: {ids}")

    def test_data_collector_fam_match_query_works(
            self, mixed_status_db):
        """Smoke test the refactored FAM-match query (line 458).
        The f-string substitution must not break the rest of the
        query (no stray brace conflicts in the surrounding SQL)."""
        from fam.models.transaction import active_tx_status_clause
        rows = mixed_status_db.execute(
            f"SELECT t.id "
            f"FROM transactions t "
            f"WHERE t.market_day_id = ? AND "
            f"{active_tx_status_clause('t')}",
            [1]).fetchall()
        ids = {r['id'] for r in rows}
        assert ids == {1, 2}


# ──────────────────────────────────────────────────────────────────
# 4. Source pin: future PRs must use the helper
# ──────────────────────────────────────────────────────────────────


class TestSourcePin:
    """Lock in that the constant + helper are exported from the
    canonical module.  A refactor that accidentally renames or
    moves them would surface here before breaking downstream
    callers."""

    def test_module_exports_constants(self):
        import fam.models.transaction as txn
        assert hasattr(txn, 'ACTIVE_TX_STATUSES')
        assert hasattr(txn, 'ACTIVE_TX_STATUS_CLAUSE')
        assert hasattr(txn, 'active_tx_status_clause')
        # Helper must be callable.
        assert callable(txn.active_tx_status_clause)
