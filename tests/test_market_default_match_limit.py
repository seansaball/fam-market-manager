"""New markets default to $100 daily match cap, not $1
(v2.0.1 fix, 2026-05-01).

Background:
  The schema's column DEFAULT for ``markets.daily_match_limit`` is
  ``INTEGER DEFAULT 10000`` (10000 cents = $100) on fresh installs.
  But the v4→v5 migration originally added the column with
  ``REAL DEFAULT 100.00`` (dollars).  The v21→v22 dollars→cents
  migration correctly multiplied existing ROWS by 100 but did NOT
  rewrite the column's DEFAULT clause — SQLite continued to coerce
  the literal float ``100.00`` to the integer ``100`` (cents) on
  every subsequent INSERT, making newly-added markets default to
  $1 on upgraded DBs.

  This test pins that ``_add_market`` (Settings → Markets → Add
  Market) sets the value EXPLICITLY to 10000 cents regardless of
  the underlying column default, so user-visible behavior is
  consistent across fresh installs and upgraded DBs.
"""

import pytest


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    from fam.database.connection import (
        set_db_path, close_connection, get_connection,
    )
    from fam.database.schema import initialize_database
    db_file = str(tmp_path / "market_default.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield get_connection()
    close_connection()


class TestNewMarketGetsHundredDollarDefault:
    """``_add_market`` writes daily_match_limit=10000 cents ($100)
    explicitly, so the value is correct even on DBs whose column
    default was corrupted by the v4→v5/v21→v22 migration sequence."""

    def test_add_market_writes_ten_thousand_cents(
            self, qtbot, empty_db, monkeypatch):
        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        screen.market_name_input.setText("New Market")
        screen.market_address_input.setText("123 Test St")
        screen._add_market()

        row = empty_db.execute(
            "SELECT daily_match_limit FROM markets "
            "WHERE name='New Market'").fetchone()
        assert row is not None, "Market was not inserted"
        assert row['daily_match_limit'] == 10000, (
            f"New market default must be 10000 cents ($100), "
            f"got {row['daily_match_limit']} cents")

    def test_add_market_with_corrupted_column_default_still_uses_100(
            self, qtbot, empty_db, monkeypatch):
        """Simulate the v4→v5 / v21→v22 corrupted-default state by
        rebuilding the markets table with ``DEFAULT 100`` (= $1
        when interpreted as cents).  ``_add_market`` must still
        write $100 because the fix sets the value explicitly."""
        # Wipe and recreate markets with a bad default to simulate
        # an upgraded DB.
        empty_db.executescript("""
            DROP TABLE IF EXISTS markets_old_save;
            ALTER TABLE markets RENAME TO markets_old_save;
            CREATE TABLE markets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                address TEXT,
                daily_match_limit INTEGER DEFAULT 100,
                match_limit_active INTEGER DEFAULT 1,
                is_active INTEGER DEFAULT 1
            );
        """)
        empty_db.commit()

        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        screen.market_name_input.setText("Upgraded-DB Market")
        screen.market_address_input.setText("456 Legacy Ave")
        screen._add_market()

        row = empty_db.execute(
            "SELECT daily_match_limit FROM markets "
            "WHERE name='Upgraded-DB Market'").fetchone()
        assert row is not None, "Market was not inserted"
        # The corrupted column default would land 100 (= $1).  The
        # fix is the EXPLICIT value in the INSERT — verify it wins.
        assert row['daily_match_limit'] == 10000, (
            f"On a DB with a bad column default, the explicit "
            f"INSERT value must still produce 10000 cents ($100); "
            f"got {row['daily_match_limit']} cents — the schema "
            f"default leaked through, defeating the fix")

    def test_audit_log_records_default_limit_dollar_amount(
            self, qtbot, empty_db):
        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        screen.market_name_input.setText("Audit-Log Market")
        screen._add_market()

        # The CREATE audit row should mention the $100 default so
        # a coordinator reading the audit log can see that the
        # default was applied (vs an explicit value).
        row = empty_db.execute(
            "SELECT new_value FROM audit_log "
            "WHERE table_name='markets' AND action='CREATE' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None
        assert '$100' in (row['new_value'] or ''), (
            f"audit_log new_value should record the default match "
            f"limit ($100); got: {row['new_value']!r}")
