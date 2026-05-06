"""Tests for the v1.9.9 device-tagged customer labels.

Background — the multi-laptop collision problem
------------------------------------------------
At one onsite market, 5 laptops were running simultaneously.  Each
laptop independently generates ``C-001``, ``C-002``, ... — meaning
"customer C-005" is ambiguous across the 5 devices.  The collision
isn't a DB constraint violation (each laptop has its own
``customer_orders`` table), it's a coordination + reporting problem:

  * Volunteers can't reliably reference a customer by ID over radio
  * The synced Google Sheets reports show 5 different rows that all
    *display* as "C-005" (separated by device_id metadata column,
    but visually identical to humans reading the report)

The fix: every customer label now carries a 1-4 char *device tag*.
Auto-derived from a SHA1 hash of the device's MachineGuid by default
(stable, zero-config), or overridden in Settings → Preferences →
Device Identity (e.g., ``LB1``).  Format: ``C-NNN-{TAG}``.

This is intentionally a CODE-ONLY change — no schema migration.
The label format evolves; the database stays at v25.  An earlier
draft introduced a UNIQUE INDEX on (market_day_id, customer_label)
but that conflicted with the legitimate "returning customer reuses
their label across multiple orders" pattern, so the index was
dropped and uniqueness is now enforced purely by construction (the
device tag in the label).

Pinned dimensions
-----------------
  1. Tag derivation (``get_device_tag``): override wins, hash
     fallback, malformed override falls through, missing device_id
     yields a sentinel.
  2. Override validation: 1-4 alphanumeric, raises on bad input,
     accepts empty/None to clear.
  3. Label format (``generate_customer_label``): ``C-NNN-{TAG}``.
  4. Settings UI source guards: tag input wired, save handler
     validates + refreshes header.
  5. Main window header source guard: device tag chip + refresh
     helper present.
"""

import inspect

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def fresh_db(tmp_path):
    db_file = str(tmp_path / "device_tag.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield tmp_path, db_file
    close_connection()


# ══════════════════════════════════════════════════════════════════
# 1. Tag derivation (auto + override resolution)
# ══════════════════════════════════════════════════════════════════
class TestDeviceTagDerivation:

    def test_auto_tag_is_three_uppercase_hex_chars(self, fresh_db):
        from fam.utils.app_settings import set_setting, get_device_tag
        set_setting('device_id', 'TEST-MACHINE-GUID-12345')
        tag = get_device_tag()
        assert len(tag) == 3, (
            f"Auto-derived tag must be exactly 3 chars (got {tag!r}); "
            f"longer tags inflate the customer label format and "
            f"shorter ones reduce the tag space below safe.")
        assert tag == tag.upper()
        # All hex chars (0-9 A-F).
        assert all(c in '0123456789ABCDEF' for c in tag)

    def test_auto_tag_is_stable_per_device(self, fresh_db):
        """Same device_id → same tag, always.  Without stability,
        labels generated before/after a settings reload could
        diverge for the same device — confusing in audit logs."""
        from fam.utils.app_settings import set_setting, get_device_tag
        set_setting('device_id', 'STABLE-DEVICE-12345')
        first = get_device_tag()
        second = get_device_tag()
        assert first == second

    def test_different_device_ids_get_different_tags(self, fresh_db):
        """Spot-check that the hash actually varies — if the
        derivation were broken (e.g. always returning the same 3
        chars), the multi-laptop fix would fail silently."""
        from fam.utils.app_settings import set_setting, get_device_tag
        set_setting('device_id', 'DEVICE-A-FULL-MACHINE-GUID')
        tag_a = get_device_tag()
        set_setting('device_id', 'DEVICE-B-FULL-MACHINE-GUID')
        tag_b = get_device_tag()
        assert tag_a != tag_b, (
            "Different device_ids must produce different tags — "
            "if they don't, the customer-label collision fix is "
            "no fix at all.")

    def test_override_wins_over_auto(self, fresh_db):
        from fam.utils.app_settings import (
            set_setting, set_device_tag_override, get_device_tag,
        )
        set_setting('device_id', 'TEST-DEVICE')
        auto_tag = get_device_tag()
        set_device_tag_override('LB1')
        assert get_device_tag() == 'LB1'
        assert get_device_tag() != auto_tag

    def test_invalid_stored_override_falls_back_to_auto(self, fresh_db):
        """If the override row in app_settings is somehow corrupted
        (manual DB edit, partial migration), the read path must
        fall back to the auto-derived tag rather than emit a
        malformed customer label."""
        from fam.utils.app_settings import set_setting, get_device_tag
        set_setting('device_id', 'TEST-DEVICE')
        auto_tag = get_device_tag()
        # Inject a malformed override (too long, contains spaces).
        set_setting('device_tag_override', 'NOT VALID 12345')
        assert get_device_tag() == auto_tag

    def test_missing_device_id_returns_sentinel(self, fresh_db):
        """No device_id captured yet → sentinel ``X00`` rather
        than crashing label generation.  In production this is
        unreachable (capture runs at app startup), but defensive."""
        from fam.utils.app_settings import get_device_tag
        # No device_id set in fresh DB.
        assert get_device_tag() == 'X00'


# ══════════════════════════════════════════════════════════════════
# 2. Override validation
# ══════════════════════════════════════════════════════════════════
class TestDeviceTagOverrideValidation:

    def test_accepts_one_to_four_alphanumeric(self, fresh_db):
        from fam.utils.app_settings import (
            set_device_tag_override, get_device_tag_override,
        )
        for valid in ('L', 'L1', 'LB1', 'MGR1', 'a1b'):
            set_device_tag_override(valid)
            assert get_device_tag_override() == valid.upper()

    def test_rejects_too_long(self, fresh_db):
        from fam.utils.app_settings import set_device_tag_override
        with pytest.raises(ValueError, match='1-4 char'):
            set_device_tag_override('TOOLONG')

    def test_rejects_zero_length(self, fresh_db):
        """Empty string is treated as 'clear', not invalid — but
        whitespace-only after stripping should also clear cleanly
        (not raise)."""
        from fam.utils.app_settings import (
            set_device_tag_override, get_device_tag_override,
        )
        set_device_tag_override('LB1')
        # Empty clears.
        set_device_tag_override('')
        assert get_device_tag_override() is None
        set_device_tag_override('LB1')
        # Whitespace also clears.
        set_device_tag_override('   ')
        assert get_device_tag_override() is None

    def test_rejects_punctuation(self, fresh_db):
        from fam.utils.app_settings import set_device_tag_override
        for invalid in ('LB-1', 'LB!', 'L 1', 'L.1'):
            with pytest.raises(ValueError):
                set_device_tag_override(invalid)

    def test_clear_via_none(self, fresh_db):
        from fam.utils.app_settings import (
            set_device_tag_override, get_device_tag_override,
        )
        set_device_tag_override('LB1')
        assert get_device_tag_override() == 'LB1'
        set_device_tag_override(None)
        assert get_device_tag_override() is None


# ══════════════════════════════════════════════════════════════════
# 3. Customer label format
# ══════════════════════════════════════════════════════════════════
class TestCustomerLabelFormat:

    def _seed_market_day(self, conn):
        conn.execute("INSERT INTO markets (id, name) VALUES (1, 'M')")
        conn.execute(
            "INSERT INTO market_days (id, market_id, date, status, "
            " opened_by) VALUES (1, 1, '2026-04-29', 'Open', 'T')")
        conn.commit()

    def test_label_includes_device_tag_suffix(self, fresh_db):
        from fam.utils.app_settings import set_setting, get_device_tag
        from fam.models.customer_order import generate_customer_label
        conn = get_connection()
        self._seed_market_day(conn)
        set_setting('device_id', 'KNOWN-DEVICE')
        tag = get_device_tag()
        label = generate_customer_label(market_day_id=1)
        assert label == f'C-001-{tag}', (
            f"Expected 'C-001-{tag}', got {label!r}.  Format change "
            f"is the entire point of the multi-laptop fix.")

    def test_sequence_increments_per_market_day(self, fresh_db):
        """Sequence is per-market-day on this device — so the
        second customer on the same day is C-002-{TAG}, not C-001.
        Confirms we didn't accidentally make the tag part of the
        uniqueness key (which would reset NNN to 001 on every
        manual override change)."""
        from fam.utils.app_settings import set_setting, set_device_tag_override
        from fam.models.customer_order import (
            generate_customer_label, create_customer_order,
        )
        conn = get_connection()
        self._seed_market_day(conn)
        set_setting('device_id', 'D')
        # First order uses auto tag.
        oid1, label1 = create_customer_order(market_day_id=1)
        assert label1.startswith('C-001-')
        # Change override; second order should still increment.
        set_device_tag_override('LB1')
        label2 = generate_customer_label(market_day_id=1)
        assert label2 == 'C-002-LB1'

    def test_two_devices_produce_disambiguated_labels(self, fresh_db):
        """The whole point of the fix.  Same market day, same
        sequence number on each device → DIFFERENT labels because
        the tags differ."""
        from fam.utils.app_settings import set_setting, get_device_tag
        from fam.models.customer_order import generate_customer_label
        conn = get_connection()
        self._seed_market_day(conn)

        # Device A.
        set_setting('device_id', 'LAPTOP-A-MACHINE-GUID')
        tag_a = get_device_tag()
        label_a = generate_customer_label(market_day_id=1)

        # Device B (simulate by changing the device_id without
        # actually inserting any rows — generate_customer_label
        # reads its tag fresh each call).
        set_setting('device_id', 'LAPTOP-B-MACHINE-GUID')
        tag_b = get_device_tag()
        label_b = generate_customer_label(market_day_id=1)

        assert tag_a != tag_b
        assert label_a != label_b, (
            "Same market day + same sequence on different devices "
            "must yield different labels — that's the collision "
            "fix.  If this fails, multi-laptop deployments still "
            "see ambiguous customer IDs.")


# ══════════════════════════════════════════════════════════════════
# 4. Same-device-same-day reuse is allowed (returning customer)
# ══════════════════════════════════════════════════════════════════
class TestReturningCustomerLabelReuse:
    """A single customer can have multiple ``customer_orders`` rows
    on the same market day with the SAME ``customer_label`` —
    that's how the "returning customer" feature works.  An earlier
    iteration of this fix added a UNIQUE INDEX on (market_day_id,
    customer_label) which broke this pattern; the test below pins
    that the index is NOT present so the legitimate reuse pattern
    keeps working."""

    def test_no_unique_index_on_market_day_label(self, fresh_db):
        conn = get_connection()
        idx = conn.execute(
            "SELECT name FROM sqlite_master "
            " WHERE type='index' "
            "   AND name='idx_customer_orders_unique_label'"
        ).fetchone()
        assert idx is None, (
            "There must be NO unique index on "
            "(market_day_id, customer_label) — the returning-"
            "customer flow legitimately creates multiple orders "
            "with the same label per market day.")

    def test_returning_customer_reuse_allowed(self, fresh_db):
        """Two orders for the same returning customer on the same
        market day must both insert cleanly."""
        conn = get_connection()
        conn.execute("INSERT INTO markets (id, name) VALUES (1, 'M')")
        conn.execute(
            "INSERT INTO market_days (id, market_id, date) VALUES "
            "(1, 1, '2026-04-29')")
        conn.execute(
            "INSERT INTO customer_orders (market_day_id, "
            " customer_label) VALUES (1, 'C-001-A1B')")
        conn.execute(
            "INSERT INTO customer_orders (market_day_id, "
            " customer_label) VALUES (1, 'C-001-A1B')")
        conn.commit()
        # Two rows with the same label is valid.
        rows = conn.execute(
            "SELECT COUNT(*) FROM customer_orders "
            " WHERE customer_label = 'C-001-A1B'"
        ).fetchone()
        assert rows[0] == 2


# ══════════════════════════════════════════════════════════════════
# 4b. v26 → v27 cleanup migration
# ══════════════════════════════════════════════════════════════════
class TestV26CleanupMigration:
    """A short-lived in-flight build of v1.9.9 stamped some installs
    with ``schema_version = 26`` and (sometimes) installed a
    ``UNIQUE INDEX idx_customer_orders_unique_label`` that broke
    returning-customer reuse.  When that build was reverted, those
    installs refused to launch ("DB v26 newer than app v25") AND
    carried a latent index that would break the next returning-
    customer attempt.

    The v27 cleanup migration drops the rogue index defensively and
    bumps the schema to 27.  These tests pin the recovery path so
    a future bisect can't accidentally remove it before every
    install in the wild has reached v27."""

    def test_schema_version_is_27(self, fresh_db):
        # v1.9.10 bumped to 28 (per-line invariant trigger).
        # Tripwire: schema reached at least the v27 cleanup step
        # AND CURRENT_SCHEMA_VERSION matches what's recorded.
        from fam.database.schema import CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION >= 27
        conn = get_connection()
        row = conn.execute(
            "SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] == CURRENT_SCHEMA_VERSION

    def test_cleanup_drops_legacy_index(self, tmp_path):
        """Simulate the user's pain: a DB stamped at v26 with the
        rogue unique index.  Running ``initialize_database`` must
        drop the index, bump to v27, and leave the app launchable."""
        from fam.database.schema import (
            initialize_database, _migrate_v26_to_v27,
        )
        import sqlite3

        db = str(tmp_path / "v26_with_index.db")
        close_connection()
        set_db_path(db)

        # Hand-build a v25-equivalent schema, populate it as if
        # the buggy v26 build had run + installed the unique index.
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        c.executescript("""
            CREATE TABLE markets (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE market_days (
              id INTEGER PRIMARY KEY,
              market_id INTEGER,
              date TEXT
            );
            CREATE TABLE customer_orders (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              market_day_id INTEGER NOT NULL,
              customer_label TEXT NOT NULL
            );
            CREATE UNIQUE INDEX idx_customer_orders_unique_label
              ON customer_orders(market_day_id, customer_label);
            CREATE TABLE schema_version (
              version INTEGER, applied_at TEXT
            );
            INSERT INTO schema_version (version) VALUES (26);
        """)
        c.commit()
        c.close()

        # Drive the cleanup migration directly to keep this test
        # focused on the recovery semantics (not the rest of the
        # migration chain, which has plenty of other coverage).
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        _migrate_v26_to_v27(c)
        idx = c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            " AND name='idx_customer_orders_unique_label'"
        ).fetchone()
        assert idx is None, (
            "_migrate_v26_to_v27 must drop the rogue unique index "
            "so installs that ran the abandoned v26 build don't "
            "carry latent breakage for returning-customer reuse.")
        c.close()

    def test_cleanup_is_idempotent_on_clean_db(self, fresh_db):
        """Fresh installs reach v27 via the fresh-install fast
        path (no per-version migrations).  The cleanup must be
        safe to run regardless: it's a ``DROP INDEX IF EXISTS``,
        which is a no-op when nothing's there."""
        from fam.database.schema import _migrate_v26_to_v27
        conn = get_connection()
        # Should not raise even though the index never existed.
        _migrate_v26_to_v27(conn)


# ══════════════════════════════════════════════════════════════════
# 5. Settings UI source guards
# ══════════════════════════════════════════════════════════════════
class TestSettingsTagEditorSourceGuards:
    """Behaviour-test the device tag editor would require building
    the full SettingsScreen + Qt event loop.  Source guards keep
    the wiring honest at near-zero cost."""

    def test_preferences_tab_constructs_tag_input(self):
        from fam.ui.settings_screen import SettingsScreen
        src = inspect.getsource(SettingsScreen._build_preferences_tab)
        assert 'self._device_tag_display' in src
        assert 'self._device_tag_input' in src
        # Override input must enforce the 4-char ceiling at the
        # widget level so coordinators can't paste a 50-char string
        # past the model-layer validator.
        assert 'setMaxLength(4)' in src

    def test_save_handler_validates_and_refreshes_header(self):
        from fam.ui.settings_screen import SettingsScreen
        src = inspect.getsource(
            SettingsScreen._save_device_tag_override)
        # ValueError surfaces as a QMessageBox, not a stack trace.
        assert 'QMessageBox.warning' in src
        # And after a successful save the main-window header chip
        # is refreshed so the new tag takes effect immediately.
        assert 'refresh_device_tag_display' in src


class TestMainWindowHeaderChip:

    def test_header_renders_device_tag_label(self):
        import fam.ui.main_window as mw
        src = inspect.getsource(mw)
        assert 'self._device_tag_label' in src, (
            "MainWindow must render a device tag chip in the header "
            "so coordinators see at a glance which device they're "
            "on — without it, the multi-laptop fix is invisible "
            "until a customer label is generated.")
        assert 'get_device_tag' in src

    def test_header_has_refresh_helper(self):
        from fam.ui.main_window import MainWindow
        # Method exists.
        assert hasattr(MainWindow, 'refresh_device_tag_display')
        src = inspect.getsource(MainWindow.refresh_device_tag_display)
        # Re-reads the active tag (so an override change takes
        # effect without restart).
        assert 'get_device_tag' in src
        assert 'setText' in src
