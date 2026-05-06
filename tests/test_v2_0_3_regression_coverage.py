"""Regression tests for v2.0.2 / v2.0.3 fixes whose failure paths
the original test suite did not exercise.

Identified by the v2.0.2 test-quality audit: the v2.0.2 fix bundle
verified the happy paths of each H-fix, but several failure paths
(rollback / abort / propagation) lacked tests that would actually
fail without the fix.  These tests close those gaps.

Test naming follows the audit's TEST-CRIT classification:
  * F-H1   penny-rec dict-level customer_total_paid recompute
  * UF-H6  audit log no-double-emit on adjustment
  * UF-H10 rewards-write rollback on failure
  * DB-H2  pre-migration backup failure RuntimeError propagation
  * UI-H8  match cap re-check at AdjustmentDialog accept (logic-only)
  * UF-H1  void rollback when update_customer_order_status raises
  * B-H8   hostname fallback rejected by get_device_id

Plus CRIT-SEC-2: photo path traversal validation tests.
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


def _seed_payment_method(conn, name='Cash', match_percent=0):
    """Insert a non-UF payment method since fresh-install only
    seeds 'Unallocated Funds' (system method)."""
    cur = conn.execute(
        "INSERT INTO payment_methods (name, match_percent, is_active) "
        "VALUES (?, ?, 1)",
        (name, match_percent))
    conn.commit()
    return {
        'id': cur.lastrowid, 'name': name,
        'match_percent': match_percent,
    }


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_v2_0_3_regression.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield tmp_path
    close_connection()


# ─── F-H1 dict-level customer_total_paid recompute ────────────────────


class TestPennyRecCustomerTotalRecompute:
    """The negative-match-guard else branch in
    ``calculate_payment_breakdown`` mutates ``customer_charged`` on
    the chosen target row.  Pre-v2.0.2 the recompute block did NOT
    re-sum ``customer_total_paid`` after the mutation, so the dict
    returned to the caller carried a STALE value — the validation
    block at the same function then reported a spurious is_valid=False
    when comparing customer + match against receipt_total."""

    def test_dict_customer_total_paid_consistent_with_line_items(self):
        """For ANY result of calculate_payment_breakdown,
        ``customer_total_paid`` MUST equal the sum of the returned
        ``line_items[*].customer_charged``.  A stale value here is
        the F-H1 bug-shape (dict carries pre-mutation sum, line_items
        carry post-mutation values)."""
        from fam.utils.calculations import calculate_payment_breakdown

        # Construct a scenario that exercises penny reconciliation
        # via the negative-match-guard else branch: one matched row
        # with a small match_amount that would go negative if the
        # full allocation_remaining were absorbed there.
        payment_entries = [
            {
                'method_amount': 1001,       # 1¢ over receipt_total
                'match_percent': 100,
                'denomination': None,
            },
        ]
        result = calculate_payment_breakdown(
            receipt_total=1000, payment_entries=payment_entries)

        actual_customer_sum = sum(
            li['customer_charged'] for li in result['line_items'])
        assert result['customer_total_paid'] == actual_customer_sum, (
            f"customer_total_paid stale after penny-rec mutation: "
            f"dict says {result['customer_total_paid']}, "
            f"actual post-mutation sum is {actual_customer_sum}.  "
            f"This is the F-H1 regression — the engine must recompute "
            f"customer_total_paid alongside allocated_total and "
            f"fam_subsidy_total whenever line_items are mutated.")

    def test_no_spurious_invalid_after_negative_match_guard(self):
        """The validation `customer_total_paid + fam_subsidy_total
        ≈ receipt_total` block at the end of the function must not
        trip on F-H1 stale values."""
        from fam.utils.calculations import calculate_payment_breakdown

        # The exact scenario that motivated F-H1: matched row whose
        # match_amount + allocation_remaining would go negative.
        payment_entries = [
            {
                'method_amount': 1001, 'match_percent': 100,
                'denomination': None,
            },
        ]
        result = calculate_payment_breakdown(
            receipt_total=1000, payment_entries=payment_entries)
        # No spurious "customer paid + match != receipt" error
        for err in result.get('errors', []):
            assert 'does not match receipt total' not in err.lower(), (
                f"Spurious is_valid=False from stale customer_total_paid: "
                f"{err}")


# ─── UF-H6 audit log no-double-emit on adjustment ─────────────────────


class TestAdjustmentAuditNoDoubleEmit:
    """Pre-v2.0.2 ``_adjust_transaction`` called ``log_action(...)``
    explicitly THEN called ``update_transaction`` without
    ``_skip_audit=True`` — so the model wrote a SECOND row per
    changed field.  Each adjustment produced 2-4 audit rows per
    changed field instead of 1, polluting Activity Log + audit_log
    table-scan health at scale."""

    def test_update_transaction_with_skip_audit_emits_no_field_rows(self):
        from fam.models.transaction import update_transaction
        from fam.utils.app_settings import set_setting

        conn = get_connection()
        set_setting('market_code', 'TST')
        set_setting('device_id', 'a1b2-real-machine-guid')
        market_id = conn.execute(
            "INSERT INTO markets (name, daily_match_limit) "
            " VALUES ('Test', 10000)").lastrowid
        vendor_id = conn.execute(
            "INSERT INTO vendors (name) VALUES ('Test V')").lastrowid
        md_id = conn.execute(
            "INSERT INTO market_days (market_id, date, status, opened_by) "
            " VALUES (?, '2026-05-05', 'Open', 'Test')",
            (market_id,)
        ).lastrowid
        cur = conn.execute(
            "INSERT INTO transactions "
            " (market_day_id, vendor_id, receipt_total, status, "
            "  fam_transaction_id) "
            " VALUES (?, ?, 2500, 'Confirmed', 'FAM-TST-X-1')",
            (md_id, vendor_id))
        txn_id = cur.lastrowid
        conn.commit()

        before = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE record_id = ?",
            (txn_id,)
        ).fetchone()[0]

        update_transaction(
            txn_id, receipt_total=3000,
            commit=True, _skip_audit=True)

        after = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE record_id = ?",
            (txn_id,)
        ).fetchone()[0]
        delta = after - before
        assert delta == 0, (
            f"_skip_audit=True must suppress per-field UPDATE rows; "
            f"got {delta} new audit rows.  This breaks UF-H6: "
            f"AdjustmentDialog emits ONE explicit ADJUST row per "
            f"changed field, then calls update_transaction with "
            f"_skip_audit=True so the audit log gets ONE row total, "
            f"not 2-4.")

    def test_update_transaction_without_skip_audit_emits_one_field_row(self):
        """Sanity check: the absence of _skip_audit produces exactly
        one UPDATE audit row per changed field (the legacy correct
        behavior that AdjustmentDialog stacked on top of)."""
        from fam.models.transaction import update_transaction
        from fam.utils.app_settings import set_setting

        conn = get_connection()
        set_setting('device_id', 'a1b2-real-machine-guid')
        market_id = conn.execute(
            "INSERT INTO markets (name, daily_match_limit) "
            " VALUES ('Test2', 10000)").lastrowid
        vendor_id = conn.execute(
            "INSERT INTO vendors (name) VALUES ('Test V2')").lastrowid
        md_id = conn.execute(
            "INSERT INTO market_days (market_id, date, status, opened_by) "
            " VALUES (?, '2026-05-05', 'Open', 'Test')",
            (market_id,)
        ).lastrowid
        cur = conn.execute(
            "INSERT INTO transactions "
            " (market_day_id, vendor_id, receipt_total, status, "
            "  fam_transaction_id) "
            " VALUES (?, ?, 2500, 'Confirmed', 'FAM-TST-X-2')",
            (md_id, vendor_id))
        txn_id = cur.lastrowid
        conn.commit()

        before = conn.execute(
            "SELECT COUNT(*) FROM audit_log "
            " WHERE record_id = ? AND action = 'UPDATE'",
            (txn_id,)
        ).fetchone()[0]

        update_transaction(txn_id, receipt_total=3000, commit=True)

        after = conn.execute(
            "SELECT COUNT(*) FROM audit_log "
            " WHERE record_id = ? AND action = 'UPDATE'",
            (txn_id,)
        ).fetchone()[0]
        assert after - before == 1


# ─── UF-H10 rewards rollback on failure ───────────────────────────────


class TestRewardsRollbackOnFailure:
    """If ``record_generated_rewards`` raises during
    ``_confirm_payment``, the entire payment commit MUST roll back.
    Pre-v2.0.2 the inner try/except swallowed the exception, the
    outer commit() ran, and payment + line items persisted while
    the rewards rows were silently lost — clerks then handed out
    physical tokens that had no DB record."""

    def test_payment_commit_rolls_back_when_inner_step_raises(self):
        """Verify the rollback contract: a transaction begun in
        ``_confirm_payment``-like fashion that raises before commit
        leaves NO rows persistent.  This is the contract that pre-fix
        was violated by the swallowed exception."""
        from fam.models.transaction import save_payment_line_items
        from fam.utils.app_settings import set_setting

        conn = get_connection()
        set_setting('market_code', 'TST')
        set_setting('device_id', 'a1b2-real-machine-guid')
        market_id = conn.execute(
            "INSERT INTO markets (name, daily_match_limit) "
            " VALUES ('TestMkt', 10000)").lastrowid
        vendor_id = conn.execute(
            "INSERT INTO vendors (name) VALUES ('V1')").lastrowid
        pm = _seed_payment_method(conn, name='Cash', match_percent=0)
        md_id = conn.execute(
            "INSERT INTO market_days (market_id, date, status, opened_by) "
            " VALUES (?, '2026-05-05', 'Open', 'Test')",
            (market_id,)
        ).lastrowid
        # Create a draft txn directly so we control the lifecycle
        cur = conn.execute(
            "INSERT INTO transactions "
            " (market_day_id, vendor_id, receipt_total, status, "
            "  fam_transaction_id) "
            " VALUES (?, ?, 2500, 'Draft', 'FAM-TST-RB-1')",
            (md_id, vendor_id))
        txn_id = cur.lastrowid
        conn.commit()

        # Now simulate the confirm-time atomic transaction:
        #   save_payment_line_items → simulated reward failure → rollback
        try:
            save_payment_line_items(txn_id, [
                {
                    'payment_method_id': pm['id'],
                    'method_name_snapshot': pm['name'],
                    'match_percent_snapshot': pm['match_percent'],
                    'method_amount': 2500,
                    'customer_charged': 2500,
                    'match_amount': 0,
                    'sort_order': 0,
                },
            ], commit=False)
            # Simulate rewards write failure (the exact failure mode
            # UF-H10 protects against)
            raise RuntimeError("simulated rewards write failure")
        except RuntimeError:
            conn.rollback()

        # No payment_line_items should persist
        plis = conn.execute(
            "SELECT id FROM payment_line_items "
            " WHERE transaction_id = ?", (txn_id,)
        ).fetchall()
        assert len(plis) == 0, (
            "Payment line items persisted despite rollback — "
            "UF-H10 contract violated.  The whole confirm bundle "
            "(line items + rewards) must succeed atomically.")

    def test_confirm_payment_no_inner_swallow_around_rewards(self):
        """Source-pin: verify NO try/except is wrapped DIRECTLY
        around the ``record_generated_rewards`` call in
        ``_confirm_payment``.  An adjacent try/except is the bug-
        shape this test guards against."""
        import inspect
        import fam.ui.payment_screen as ps
        src = inspect.getsource(ps)
        confirm_idx = src.find('def _confirm_payment(')
        assert confirm_idx != -1
        next_def = src.find('\n    def ', confirm_idx + 1)
        body = src[confirm_idx:next_def if next_def > 0 else len(src)]

        # Find the call to record_generated_rewards
        idx = body.find('record_generated_rewards(')
        assert idx != -1, (
            "Could not locate record_generated_rewards call site "
            "inside _confirm_payment")

        # Walk a small window before the call looking for the
        # antipattern: a `try:` immediately preceding it that wraps
        # only this call.  The legitimate outer `try:` is far above.
        # Look at the ~15 lines before the call.
        lookback = body[max(0, idx - 600): idx]
        # Count `try:` in the recent window — the outer try is
        # OUTSIDE the lookback if positioned correctly.
        # Specifically the antipattern looks like:
        #     try:
        #         from fam.models... import record_generated_rewards
        #         record_generated_rewards(...)
        #     except Exception:
        #         logger.exception(...)
        # We detect this by checking if there's a `try:` close to
        # the call AND an `except` close after the call.
        last_try = lookback.rfind('\n                try:')
        # Allow `try:` if it's the OUTER one (further away than the
        # 'if reward_lines and ...' guard line).  The post-fix code
        # has no inner try, so `last_try` should be far away or
        # absent.
        if last_try != -1:
            # If the try: appears within 5 lines of the call, that
            # IS the antipattern.
            chars_between = idx - (max(0, idx - 600) + last_try)
            assert chars_between > 200, (
                f"Inner `try:` found within {chars_between} chars of "
                f"record_generated_rewards call — UF-H10 swallow "
                f"regression.  The reward write must propagate to "
                f"the OUTER try so conn.rollback() fires.")


# ─── DB-H2 pre-migration backup failure RuntimeError propagation ──────


class TestPreMigrationBackupFailureFatal:
    """If ``_write_pre_migration_backup`` raises (disk full, AV
    interference, locked file), pre-v2.0.2 init swallowed the warning
    and proceeded with the migration anyway.  Destructive migrations
    (e.g. the historical v21→v22 cents conversion) are irreversible
    without the snapshot, so this is now a FATAL condition."""

    def test_initialize_database_propagates_pre_migration_backup_failure(
            self, tmp_path):
        """Construct a synthetic pre-v34 DB, monkeypatch
        ``_write_pre_migration_backup`` to raise OSError, run
        ``initialize_database``, and assert RuntimeError propagates."""
        import fam.database.schema as schema_mod
        from fam.database.connection import (
            close_connection, set_db_path,
        )

        close_connection()
        legacy_path = str(tmp_path / "legacy_pre_v34.db")
        # Build a synthetic pre-v34 DB so initialize_database
        # decides an upgrade is required (skips fresh-install path)
        legacy = sqlite3.connect(legacy_path)
        legacy.execute(
            "CREATE TABLE schema_version (version INTEGER, "
            " applied_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        legacy.execute(
            "INSERT INTO schema_version (version) VALUES (33)")
        legacy.commit()
        legacy.close()

        set_db_path(legacy_path)

        original = schema_mod._write_pre_migration_backup

        def boom(*a, **kw):
            raise OSError("simulated disk full")
        schema_mod._write_pre_migration_backup = boom

        try:
            with pytest.raises(RuntimeError) as exc_info:
                schema_mod.initialize_database()
            assert ('pre-migration backup' in str(exc_info.value).lower()
                    or 'backup' in str(exc_info.value).lower())
        finally:
            schema_mod._write_pre_migration_backup = original


# ─── UI-H8 match cap re-check helper ──────────────────────────────────


class TestRecomputeMatchLimitForTxn:
    """The post-accept TOCTOU re-check helper must:
    - Return None for markets without an active match cap.
    - Return ``max(0, daily_cap - prior_match_excluding_this_txn)``
      otherwise.
    - Use the current DB state, not a snapshot."""

    def test_returns_none_when_no_active_cap(self):
        from fam.ui.admin_screen import _recompute_match_limit_for_txn

        conn = get_connection()
        market_id = conn.execute(
            "INSERT INTO markets "
            " (name, daily_match_limit, match_limit_active) "
            " VALUES ('M', 10000, 0)").lastrowid
        vendor_id = conn.execute(
            "INSERT INTO vendors (name) VALUES ('V')").lastrowid
        md_id = conn.execute(
            "INSERT INTO market_days "
            " (market_id, date, status, opened_by) "
            " VALUES (?, '2026-05-05', 'Open', 'T')",
            (market_id,)
        ).lastrowid
        cur = conn.execute(
            "INSERT INTO transactions "
            " (market_day_id, vendor_id, receipt_total, status, "
            "  fam_transaction_id) "
            " VALUES (?, ?, 2500, 'Confirmed', 'FAM-T-X-1')",
            (md_id, vendor_id))
        conn.commit()

        txn = conn.execute(
            "SELECT id, market_day_id FROM transactions "
            " WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        result = _recompute_match_limit_for_txn(txn)
        assert result is None

    def test_returns_remaining_cap_excluding_this_txn(self):
        from fam.ui.admin_screen import _recompute_match_limit_for_txn

        conn = get_connection()
        market_id = conn.execute(
            "INSERT INTO markets "
            " (name, daily_match_limit, match_limit_active) "
            " VALUES ('M2', 10000, 1)").lastrowid
        vendor_id = conn.execute(
            "INSERT INTO vendors (name) VALUES ('V2')").lastrowid
        md_id = conn.execute(
            "INSERT INTO market_days "
            " (market_id, date, status, opened_by) "
            " VALUES (?, '2026-05-05', 'Open', 'T')",
            (market_id,)
        ).lastrowid
        co_id = conn.execute(
            "INSERT INTO customer_orders "
            " (market_day_id, customer_label, status) "
            " VALUES (?, 'C-001', 'Confirmed')",
            (md_id,)
        ).lastrowid
        prior_txn = conn.execute(
            "INSERT INTO transactions "
            " (market_day_id, vendor_id, customer_order_id, "
            "  receipt_total, status, fam_transaction_id) "
            " VALUES (?, ?, ?, 6000, 'Confirmed', 'FAM-T-X-2')",
            (md_id, vendor_id, co_id)
        ).lastrowid
        pm = _seed_payment_method(conn, name='Cash', match_percent=100)
        conn.execute(
            "INSERT INTO payment_line_items "
            " (transaction_id, payment_method_id, method_name_snapshot, "
            "  match_percent_snapshot, method_amount, customer_charged, "
            "  match_amount) "
            " VALUES (?, ?, ?, ?, 6000, 3000, 3000)",
            (prior_txn, pm['id'], pm['name'], pm['match_percent']))
        cur_txn = conn.execute(
            "INSERT INTO transactions "
            " (market_day_id, vendor_id, customer_order_id, "
            "  receipt_total, status, fam_transaction_id) "
            " VALUES (?, ?, ?, 4000, 'Confirmed', 'FAM-T-X-3')",
            (md_id, vendor_id, co_id)
        ).lastrowid
        conn.commit()

        txn = conn.execute(
            "SELECT id, market_day_id FROM transactions "
            " WHERE id = ?", (cur_txn,)
        ).fetchone()
        result = _recompute_match_limit_for_txn(txn)
        # Cap = $100 = 10000 cents, prior consumed = $30 = 3000
        # cents (excluding cur_txn), so remaining = 7000.
        assert result == 7000, (
            f"Expected 7000 cents remaining cap, got {result}.  "
            f"Helper must compute daily_cap - prior_match where prior "
            f"excludes this txn (UI-H8 contract).")


# ─── UF-H1 / UF-H2 atomic void rollback ───────────────────────────────


class TestVoidTransactionCommitFalse:
    """``void_transaction(commit=False)`` must NOT commit the void;
    the caller is responsible.  A subsequent caller-driven rollback
    must undo the void."""

    def test_void_rollback_undoes_status_change(self):
        from fam.models.transaction import void_transaction
        from fam.utils.app_settings import set_setting

        conn = get_connection()
        set_setting('device_id', 'a1b2-real-machine-guid')
        market_id = conn.execute(
            "INSERT INTO markets (name, daily_match_limit) "
            " VALUES ('VR', 10000)").lastrowid
        vendor_id = conn.execute(
            "INSERT INTO vendors (name) VALUES ('VR')").lastrowid
        md_id = conn.execute(
            "INSERT INTO market_days "
            " (market_id, date, status, opened_by) "
            " VALUES (?, '2026-05-05', 'Open', 'T')",
            (market_id,)
        ).lastrowid
        cur = conn.execute(
            "INSERT INTO transactions "
            " (market_day_id, vendor_id, receipt_total, status, "
            "  fam_transaction_id) "
            " VALUES (?, ?, 2500, 'Confirmed', 'FAM-T-VR-1')",
            (md_id, vendor_id))
        txn_id = cur.lastrowid
        conn.commit()

        # Caller-managed transaction: void + simulated subsequent
        # failure + rollback
        try:
            void_transaction(txn_id, voided_by='Test', commit=False)
            raise RuntimeError("simulated order-status flip failure")
        except RuntimeError:
            conn.rollback()

        # The void must be undone — txn back to Confirmed
        status = conn.execute(
            "SELECT status FROM transactions WHERE id = ?", (txn_id,)
        ).fetchone()['status']
        assert status == 'Confirmed', (
            f"Expected 'Confirmed' after rollback, got {status!r}.  "
            f"UF-H1/H2 contract: void + parent-order flip must commit "
            f"or fail together.  Pre-fix, the model's internal commit "
            f"made the void durable before the rollback could undo it.")

        # And no audit row for VOID should exist
        audit_rows = conn.execute(
            "SELECT COUNT(*) FROM audit_log "
            " WHERE record_id = ? AND action = 'VOID'",
            (txn_id,)
        ).fetchone()[0]
        assert audit_rows == 0


# ─── B-H8 hostname fallback ───────────────────────────────────────────


class TestGetDeviceIdRejectsHostnameFallback:
    """``get_device_id`` returns None for the synthetic ``hostname-XXX``
    fallback so the v1.9.10 startup hard-fail catches it.  Pre-v2.0.2
    image-cloned fleet laptops sharing a hostname silently produced
    the same ``device_id`` for ALL of them."""

    def test_get_device_id_returns_none_for_hostname_placeholder(self):
        from fam.utils.app_settings import (
            get_device_id, set_setting,
        )
        # Direct write bypassing capture_device_id
        set_setting('device_id', 'hostname-DESKTOP-ABC123')
        assert get_device_id() is None, (
            "B-H8 contract: get_device_id must treat the hostname-XXX "
            "synthetic fallback as not-a-device-id so the startup "
            "hard-fail in fam/app.py catches cloned-laptop collisions.")

    def test_get_device_id_returns_real_value(self):
        from fam.utils.app_settings import (
            get_device_id, set_setting,
        )
        set_setting('device_id', 'a1b2c3d4-real-machine-guid')
        assert get_device_id() == 'a1b2c3d4-real-machine-guid'

    def test_get_device_id_returns_none_when_unset(self):
        from fam.utils.app_settings import get_device_id
        assert get_device_id() is None

    def test_is_hostname_fallback_id_helper(self):
        from fam.utils.app_settings import _is_hostname_fallback_id
        assert _is_hostname_fallback_id('hostname-DESKTOP-X')
        assert _is_hostname_fallback_id('hostname-')
        assert not _is_hostname_fallback_id('a1b2c3d4-uuid')
        assert not _is_hostname_fallback_id('')
        assert not _is_hostname_fallback_id(None)


# ─── CRIT-SEC-2 photo path traversal ─────────────────────────────────


class TestPhotoPathTraversalRejected:
    """Photo paths stored in DB rows must not be able to escape the
    data_dir on read.  Pre-v2.0.3 ``os.path.join(data_dir, abs_path)``
    returned ``abs_path`` verbatim — turning the next Drive sync
    into an arbitrary-file exfiltration."""

    def test_absolute_windows_path_rejected(self):
        from fam.utils.photo_storage import (
            _validate_relative_photo_path, UnsafePhotoPathError,
        )
        with pytest.raises(UnsafePhotoPathError):
            _validate_relative_photo_path(
                r'C:\Users\X\.aws\credentials')

    def test_absolute_unix_path_rejected(self):
        from fam.utils.photo_storage import (
            _validate_relative_photo_path, UnsafePhotoPathError,
        )
        with pytest.raises(UnsafePhotoPathError):
            _validate_relative_photo_path('/etc/passwd')

    def test_drive_letter_inside_string_rejected(self):
        from fam.utils.photo_storage import (
            _validate_relative_photo_path, UnsafePhotoPathError,
        )
        with pytest.raises(UnsafePhotoPathError):
            _validate_relative_photo_path('D:malicious')

    def test_dotdot_escape_rejected(self):
        from fam.utils.photo_storage import (
            _validate_relative_photo_path, UnsafePhotoPathError,
        )
        with pytest.raises(UnsafePhotoPathError):
            _validate_relative_photo_path('photos/../../escape.jpg')

    def test_legitimate_relative_path_accepted(self):
        from fam.utils.photo_storage import _validate_relative_photo_path
        assert _validate_relative_photo_path(
            'photos/fmnp_42_1709912345.jpg') == (
            'photos/fmnp_42_1709912345.jpg')

    def test_empty_path_accepted(self):
        from fam.utils.photo_storage import _validate_relative_photo_path
        # Empty / None means "no photo" — callers handle this
        assert _validate_relative_photo_path('') == ''

    def test_photo_exists_returns_false_for_unsafe(self):
        from fam.utils.photo_storage import photo_exists
        # On Windows this would otherwise resolve to the literal
        # absolute path.  photo_exists must refuse without raising
        # so the caller's UI flow stays stable.
        assert photo_exists(r'C:\Windows\System32\cmd.exe') is False

    def test_get_photo_full_path_raises_on_unsafe(self):
        from fam.utils.photo_storage import (
            get_photo_full_path, UnsafePhotoPathError,
        )
        with pytest.raises(UnsafePhotoPathError):
            get_photo_full_path('/etc/passwd')


# ─── NEW-CRIT-2 Activity Log LIMIT ────────────────────────────────────


class TestActivityLogLimit:
    """The Activity Log query in reports_screen must use LIMIT to
    avoid reading all of audit_log into memory at year 2-3 scale
    (500K+ rows).  Source-pin only — driving the full ReportsScreen
    needs the full Qt environment."""

    def test_load_activity_log_uses_limit(self):
        import inspect
        import fam.ui.reports_screen as rs
        src = inspect.getsource(rs)
        idx = src.find('def _load_activity_log(')
        assert idx != -1
        end = src.find('\n    def ', idx + 1)
        body = src[idx:end if end > 0 else len(src)]
        assert 'LIMIT' in body, (
            "_load_activity_log must use LIMIT — at year 2-3 scale "
            "(500K+ audit_log rows) the unbounded fetchall freezes "
            "the UI thread for 30-60s on Activity Log open.")
