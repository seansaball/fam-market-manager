"""Regression: ``get_unallocated_funds_method`` must re-seed the
system row on demand when it's missing — the manager should never
see "Unallocated Funds method missing — cannot record absorption"
when they confirm the customer-is-gone path during an adjustment.

User-reported (2026-04-30 onsite):

    "no error in the logs but received this on the adjustments page"
    [screenshot: "Unallocated Funds method missing — cannot record
                   absorption."]

  The Adjustments dialog's "customer-pay-delta + No customer gone"
  flow injects an ``Unallocated Funds`` line item to record the
  FAM-absorbed amount.  When ``get_unallocated_funds_method()``
  returned ``None`` (row missing from the DB despite the v24→v25
  migration having seeded it on startup), the dialog surfaced a
  ``QMessageBox.critical`` with the error and refused to save —
  blocking a legitimate adjustment.

Possible causes for the missing row in production:

  - A pre-v25 row at id=9999 blocked ``INSERT OR IGNORE`` from
    seeding the system row (seed uses an explicit high id to leave
    low IDs free for test fixtures).
  - The row was hand-deleted outside the app.
  - Sync/restore from an older backup overwrote it.
  - Partial migration aborted between schema-version write and the
    seed.

Fix
---
Make ``get_unallocated_funds_method`` self-healing: on a missed
lookup, re-seed the row inline (mirroring the v25 migration's seed
exactly), backfill vendor eligibility, and retry the lookup.
Return ``None`` only when both the lookup and the on-demand seeding
fail — production should never see this.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database
from fam.models.payment_method import (
    UNALLOCATED_FUNDS_NAME, get_unallocated_funds_method,
)


@pytest.fixture
def fresh_db(tmp_path):
    db_file = str(tmp_path / "uf_self_heal.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield get_connection()
    close_connection()


class TestUnallocatedFundsSelfHeal:

    def test_lookup_succeeds_on_normal_seeded_db(self, fresh_db):
        """Sanity: on a freshly-initialized DB the migration seeded
        the row and the lookup returns it without re-seeding."""
        method = get_unallocated_funds_method()
        assert method is not None
        assert method['name'] == UNALLOCATED_FUNDS_NAME
        assert method['is_system'] == 1

    def test_self_heal_re_seeds_after_row_deletion(self, fresh_db):
        """Pin the contract: if the UF row is somehow deleted from
        the DB (manual SQL, backup restore, partial migration),
        the next ``get_unallocated_funds_method`` call must re-seed
        it inline rather than returning ``None``."""
        # Confirm the migration seeded the row.
        assert get_unallocated_funds_method() is not None

        # Simulate the row going missing.
        fresh_db.execute(
            "DELETE FROM payment_methods WHERE name = ?",
            (UNALLOCATED_FUNDS_NAME,)
        )
        fresh_db.commit()
        # Direct lookup should now miss.
        row = fresh_db.execute(
            "SELECT * FROM payment_methods WHERE name = ?",
            (UNALLOCATED_FUNDS_NAME,)
        ).fetchone()
        assert row is None, (
            "Test setup: deletion should have removed the row")

        # The high-level helper must self-heal.
        method = get_unallocated_funds_method()
        assert method is not None, (
            "get_unallocated_funds_method must re-seed the row when "
            "missing rather than returning None.  Without this, the "
            "Adjustments 'customer gone' path surfaces a System Error "
            "popup the manager can't recover from.")
        assert method['name'] == UNALLOCATED_FUNDS_NAME
        assert method['is_system'] == 1

    def test_self_heal_when_id_9999_is_occupied_by_other_row(
            self, fresh_db):
        """The migration's seed targets id=9999 explicitly.  If
        some other payment method already occupies id=9999, the
        ``INSERT OR IGNORE`` silently fails.  Self-heal must fall
        back to inserting WITHOUT an explicit id so SQLite assigns
        a free one."""
        # Delete the migration-seeded UF row, then plant a different
        # method at id=9999 to block the high-id seed path.
        fresh_db.execute(
            "DELETE FROM payment_methods WHERE name = ?",
            (UNALLOCATED_FUNDS_NAME,)
        )
        fresh_db.execute(
            "INSERT INTO payment_methods "
            "(id, name, match_percent, is_active, sort_order, "
            " denomination, photo_required, is_system) "
            "VALUES (9999, 'Some Other Method', 50.0, 1, 1, "
            " NULL, NULL, 0)"
        )
        fresh_db.commit()

        method = get_unallocated_funds_method()
        assert method is not None, (
            "Self-heal must succeed even when id=9999 is occupied "
            "by another row — falls back to auto-id insert.")
        assert method['name'] == UNALLOCATED_FUNDS_NAME
        assert method['id'] != 9999, (
            f"When id=9999 is occupied, the auto-id fallback must "
            f"pick a different id.  Got id={method['id']}.")

    def test_self_heal_backfills_vendor_eligibility(self, fresh_db):
        """When the row is re-seeded, vendor_payment_methods rows
        must be backfilled so the new UF row passes the
        Adjustments eligibility guard on the very next save —
        otherwise the recovery is incomplete and the next save
        would fail with a vendor-eligibility error instead."""
        # Add a vendor.
        fresh_db.execute(
            "INSERT INTO vendors (id, name) VALUES (1, 'V1')")
        fresh_db.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (1, 1)") if False else None  # market table may not exist
        fresh_db.commit()

        # Delete the UF row and any of its vendor eligibility rows.
        uf = fresh_db.execute(
            "SELECT id FROM payment_methods WHERE name = ?",
            (UNALLOCATED_FUNDS_NAME,)).fetchone()
        if uf:
            fresh_db.execute(
                "DELETE FROM vendor_payment_methods "
                "WHERE payment_method_id = ?", (uf[0],))
            fresh_db.execute(
                "DELETE FROM payment_methods WHERE id = ?", (uf[0],))
            fresh_db.commit()

        method = get_unallocated_funds_method()
        assert method is not None

        # Vendor 1 must now be registered for the recovered UF row.
        eligible = fresh_db.execute(
            "SELECT COUNT(*) FROM vendor_payment_methods "
            " WHERE payment_method_id = ? AND vendor_id = 1",
            (method['id'],)
        ).fetchone()[0]
        assert eligible == 1, (
            "Self-heal must also backfill vendor_payment_methods "
            "for every existing vendor — otherwise the next save "
            "fails with a vendor-eligibility error rather than the "
            "original missing-method error.")

    def test_repeated_calls_idempotent(self, fresh_db):
        """Calling ``get_unallocated_funds_method`` repeatedly must
        not create duplicate rows — verifies the INSERT OR IGNORE
        guard."""
        for _ in range(5):
            method = get_unallocated_funds_method()
            assert method is not None

        count = fresh_db.execute(
            "SELECT COUNT(*) FROM payment_methods WHERE name = ?",
            (UNALLOCATED_FUNDS_NAME,)
        ).fetchone()[0]
        assert count == 1, (
            f"Expected exactly one Unallocated Funds row, got "
            f"{count}.  Self-heal must be idempotent under "
            f"repeated calls.")
