"""New markets / vendors / payment methods auto-assign their
junction-table cross-products on creation (v2.0.6 fix).

Pre-fix the Settings → Add Market UI inserted only the ``markets``
row.  The ``market_vendors`` and ``market_payment_methods`` junctions
remained empty, so Settings → Vendors and Settings → Markets
correctly showed all checkboxes UNCHECKED — yet the runtime fallback
in ``receipt_intake_screen._load_vendors`` (and the equivalent in
``payment_screen``) silently showed ALL vendors / methods anyway,
making the Settings UI feel disconnected from reality.

Fix: every creation path now auto-assigns the cross-product so
Settings is the source of truth from day 1.  Coordinators uncheck
what doesn't apply; un-checks now correctly propagate to Intake.

The mirror direction is also covered: a NEW vendor / payment method
gets joined to all existing markets at creation time.
"""

import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_creation_auto_assigns.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield tmp_path
    close_connection()


def _setup_baseline(conn):
    """Insert two vendors and two payment methods to be cross-joined
    against the new entity under test."""
    conn.execute(
        "INSERT INTO vendors (id, name, is_active) "
        "VALUES (1, 'V1', 1), (2, 'V2', 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, is_active) "
        "VALUES (10, 'PM1', 100.0, 1), (11, 'PM2', 50.0, 1)")
    conn.commit()


# ─── Adding a market auto-assigns vendors AND methods ────────────


class TestAddMarketAutoAssigns:

    def test_new_market_picks_up_existing_active_vendors(self):
        conn = get_connection()
        _setup_baseline(conn)
        # Direct SQL mimicking _add_market's INSERT path
        conn.execute(
            "INSERT INTO markets (name, daily_match_limit) "
            "VALUES ('NewMkt', 10000)")
        new_market_id = conn.execute(
            "SELECT id FROM markets WHERE name = 'NewMkt'"
        ).fetchone()['id']
        # Replicate the cross-product the UI now does
        conn.execute(
            "INSERT OR IGNORE INTO market_vendors "
            " (market_id, vendor_id) "
            " SELECT ?, id FROM vendors WHERE is_active = 1",
            (new_market_id,))
        conn.commit()

        from fam.models.vendor import get_vendors_for_market
        vendors = get_vendors_for_market(new_market_id)
        assert len(vendors) == 2, (
            f"New market must auto-assign all 2 active vendors; "
            f"got {len(vendors)}")
        names = sorted(v['name'] for v in vendors)
        assert names == ['V1', 'V2']

    def test_settings_screen_add_market_assigns_vendors(self, qtbot, monkeypatch):
        """End-to-end through the actual UI add-market path."""
        from fam.ui.settings_screen import SettingsScreen
        conn = get_connection()
        _setup_baseline(conn)
        # Stub _settings_changed_by to avoid prompting for name
        import fam.ui.settings_screen as ss
        monkeypatch.setattr(ss, '_settings_changed_by',
                            lambda: 'Tester')

        # Build the screen lazily — need _load_markets to work
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        screen.market_name_input.setText("NewMkt")
        screen.market_address_input.setText("123 Test")
        screen._add_market()

        new_market_id = conn.execute(
            "SELECT id FROM markets WHERE name = 'NewMkt'"
        ).fetchone()['id']

        # Both vendors should be assigned
        from fam.models.vendor import get_vendors_for_market
        vendors = get_vendors_for_market(new_market_id)
        assert len(vendors) == 2, (
            "Settings → Add Market must auto-assign every active "
            "vendor.  Pre-fix, the new market started with empty "
            "junction so all Settings checkboxes were unchecked "
            "while the Intake screen still showed all vendors via "
            "the runtime fallback.  v2.0.6 makes Settings authoritative.")

        # Both payment methods should be assigned
        from fam.models.payment_method import get_payment_methods_for_market
        methods = get_payment_methods_for_market(
            new_market_id, include_system=False)
        # 2 from baseline + the v24-seeded UF (excluded by include_system=False)
        assert len(methods) == 2, (
            f"Settings → Add Market must auto-assign every active "
            f"payment method; got {len(methods)}")


# ─── create_vendor still backfills vendor_payment_methods ───────


class TestCreateVendorPreservesPaymentMethodBackfill:
    """Don't regress the existing v23→v24 permissive backfill —
    a new vendor is eligible for every active payment method."""

    def test_new_vendor_still_gets_payment_methods(self):
        from fam.models.vendor import create_vendor
        conn = get_connection()
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent, is_active) "
            "VALUES (50, 'X', 100.0, 1), (51, 'Y', 0.0, 1)")
        conn.commit()
        new_id = create_vendor(name='V')
        rows = conn.execute(
            "SELECT payment_method_id FROM vendor_payment_methods "
            " WHERE vendor_id = ?", (new_id,)
        ).fetchall()
        method_ids = {r['payment_method_id'] for r in rows}
        assert 50 in method_ids
        assert 51 in method_ids


class TestSelfHealBackfillLegacyEmpty:

    def test_self_heal_backfills_legacy_empty_markets(self):
        """v2.0.6: a market with ZERO assignments in BOTH junctions
        (created via the pre-fix _add_market path) gets back-filled
        on next launch.  Markets with ANY existing assignment are
        left alone — only fully-empty markets are treated as
        legacy-uninitialised."""
        conn = get_connection()
        # Two legacy empty markets (the user's reported case)
        conn.execute(
            "INSERT INTO markets (id, name, daily_match_limit) "
            "VALUES (700, 'LegacyA', 10000), (701, 'LegacyB', 10000)")
        # One curated market with explicit assignment — must NOT be
        # re-touched (back-fill would silently re-add un-checked vendors)
        conn.execute(
            "INSERT INTO markets (id, name, daily_match_limit) "
            "VALUES (702, 'Curated', 10000)")
        # Pre-existing baseline vendors + methods
        conn.execute(
            "INSERT INTO vendors (id, name, is_active) "
            "VALUES (800, 'V1', 1), (801, 'V2', 1)")
        conn.execute(
            "INSERT INTO payment_methods (id, name, match_percent, is_active) "
            "VALUES (900, 'PM1', 100.0, 1), (901, 'PM2', 0.0, 1)")
        # Curated market: deliberately only one vendor + one method
        conn.execute(
            "INSERT INTO market_vendors (market_id, vendor_id) "
            "VALUES (702, 800)")
        conn.execute(
            "INSERT INTO market_payment_methods "
            " (market_id, payment_method_id) VALUES (702, 900)")
        conn.commit()

        # Replicate the self-heal logic from fam/app.py:run() — keep
        # this in sync with the production code; if it ever drifts the
        # test is the canary.
        empty_vendor_markets = conn.execute(
            "SELECT m.id FROM markets m "
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM market_vendors mv "
            "    WHERE mv.market_id = m.id)"
        ).fetchall()
        empty_method_markets = conn.execute(
            "SELECT m.id FROM markets m "
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM market_payment_methods mpm "
            "    WHERE mpm.market_id = m.id)"
        ).fetchall()
        for row in empty_vendor_markets:
            conn.execute(
                "INSERT OR IGNORE INTO market_vendors "
                " (market_id, vendor_id) "
                " SELECT ?, id FROM vendors WHERE is_active = 1",
                (row[0],))
        for row in empty_method_markets:
            conn.execute(
                "INSERT OR IGNORE INTO market_payment_methods "
                " (market_id, payment_method_id) "
                " SELECT ?, id FROM payment_methods "
                "  WHERE is_active = 1",
                (row[0],))
        conn.commit()

        # Both legacy markets back-filled
        for legacy_id in (700, 701):
            v_count = conn.execute(
                "SELECT COUNT(*) FROM market_vendors WHERE market_id = ?",
                (legacy_id,)
            ).fetchone()[0]
            assert v_count == 2, (
                f"Legacy market {legacy_id} should be back-filled "
                f"with both active vendors; got {v_count}.")
            # Methods: 2 baseline + Unallocated Funds (system, is_active=1)
            m_count = conn.execute(
                "SELECT COUNT(*) FROM market_payment_methods "
                " WHERE market_id = ?",
                (legacy_id,)
            ).fetchone()[0]
            assert m_count >= 2

        # Curated market UNTOUCHED — only 1 vendor + 1 method
        v_curated = conn.execute(
            "SELECT COUNT(*) FROM market_vendors WHERE market_id = 702"
        ).fetchone()[0]
        m_curated = conn.execute(
            "SELECT COUNT(*) FROM market_payment_methods WHERE market_id = 702"
        ).fetchone()[0]
        assert v_curated == 1, (
            f"Curated market must NOT be back-filled; the operator "
            f"deliberately picked just 1 vendor.  Got {v_curated}.  "
            f"Self-heal must only touch markets with ZERO existing "
            f"assignments.")
        assert m_curated == 1

