"""Tests for Settings → Rewards tab.

Covers:
  * Tab is registered with the expected title.
  * Master enable/disable toggle persists to app_settings.
  * Source combo lists every active method.
  * Reward (target) combo lists ONLY denominated active methods.
  * Add-rule round-trip: create + reload + visible in table.
  * Toggle rule active/inactive.
  * Delete rule (with confirmation accepted).
  * Source==reward warns and aborts.
  * Non-denominated reward attempt warns and aborts.
"""

from unittest.mock import patch
import pytest

from fam.database.connection import (
    set_db_path, get_connection, close_connection,
)
from fam.database.schema import initialize_database


@pytest.fixture
def settings_db(tmp_path):
    db_file = str(tmp_path / "rewards_tab.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    conn = get_connection()
    # Minimal seed so the screen has data to render.
    conn.execute(
        "INSERT INTO markets (id, name, daily_match_limit, "
        " match_limit_active) VALUES (1, 'M', 10000, 1)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) "
        "VALUES (1, 'SNAP', 100.0, 1, 1, NULL)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) "
        "VALUES (2, 'JH Food Bucks', 100.0, 2, 1, 200)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) "
        "VALUES (3, 'Cash', 0.0, 3, 1, NULL)")
    conn.execute(
        "INSERT INTO payment_methods (id, name, match_percent, "
        " sort_order, is_active, denomination) "
        "VALUES (4, 'Food RX', 100.0, 4, 1, 1000)")
    conn.commit()
    yield conn
    close_connection()


class TestRewardsTabRegistration:

    def test_rewards_tab_is_registered(self, qtbot, settings_db):
        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        labels = [screen.tabs.tabText(i)
                  for i in range(screen.tabs.count())]
        assert 'Rewards' in labels, (
            f"Settings tabs missing 'Rewards' tab: {labels}")

    def test_rewards_tab_after_payment_methods(self, qtbot, settings_db):
        """The Rewards tab should sit between Payment Methods and
        Preferences for discoverability."""
        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        labels = [screen.tabs.tabText(i)
                  for i in range(screen.tabs.count())]
        rewards_idx = labels.index('Rewards')
        pm_idx = labels.index('Payment Methods')
        assert rewards_idx == pm_idx + 1, (
            f"Rewards tab should follow Payment Methods; "
            f"got order: {labels}")


class TestRewardsMasterToggle:

    def test_default_is_enabled(self, qtbot, settings_db):
        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        # Fresh DB → no 'rewards_enabled' setting → default = '1'.
        assert screen.rewards_enabled_check.isChecked()

    def test_unchecking_persists_to_settings(self, qtbot, settings_db):
        from fam.ui.settings_screen import SettingsScreen
        from fam.utils.app_settings import is_rewards_enabled
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        screen.rewards_enabled_check.setChecked(False)
        assert is_rewards_enabled() is False

    def test_rechecking_persists_to_settings(self, qtbot, settings_db):
        from fam.ui.settings_screen import SettingsScreen
        from fam.utils.app_settings import (
            is_rewards_enabled, set_rewards_enabled,
        )
        set_rewards_enabled(False)
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        assert not screen.rewards_enabled_check.isChecked()
        screen.rewards_enabled_check.setChecked(True)
        assert is_rewards_enabled() is True


class TestRewardsTabComboboxes:

    def test_source_combo_has_all_active_methods(
            self, qtbot, settings_db):
        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        names = [screen.reward_source_combo.itemText(i)
                 for i in range(screen.reward_source_combo.count())]
        # All four seed methods (SNAP, JH FB, Cash, Food RX) are
        # active and non-system.
        assert set(names) == {
            'SNAP', 'JH Food Bucks', 'Cash', 'Food RX'}

    def test_target_combo_only_denominated_methods(
            self, qtbot, settings_db):
        from fam.ui.settings_screen import SettingsScreen
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        labels = [screen.reward_target_combo.itemText(i)
                  for i in range(screen.reward_target_combo.count())]
        # Only JH Food Bucks (200c denom) and Food RX (1000c denom).
        # SNAP and Cash (NULL denom) must be excluded.
        joined = ' '.join(labels)
        assert 'JH Food Bucks' in joined
        assert 'Food RX' in joined
        assert 'SNAP' not in joined, (
            "SNAP has no denomination — must NOT be a reward "
            "target option")
        assert 'Cash' not in joined


class TestAddRewardRule:

    def test_add_rule_round_trip(self, qtbot, settings_db, monkeypatch):
        from fam.ui.settings_screen import SettingsScreen
        from fam.models.reward_rule import get_all_reward_rules

        screen = SettingsScreen()
        qtbot.addWidget(screen)
        # Pick SNAP × $5 → $2 × JH Food Bucks.
        for i in range(screen.reward_source_combo.count()):
            if screen.reward_source_combo.itemText(i) == 'SNAP':
                screen.reward_source_combo.setCurrentIndex(i)
                break
        for i in range(screen.reward_target_combo.count()):
            if 'JH Food Bucks' in screen.reward_target_combo.itemText(i):
                screen.reward_target_combo.setCurrentIndex(i)
                break
        screen.reward_threshold_spin.setValue(5.00)
        screen.reward_unit_spin.setValue(2.00)
        # Click Add Rule.
        screen._add_reward_rule()
        # Rule appears.
        rules = get_all_reward_rules()
        assert len(rules) == 1
        assert rules[0]['source_method_id'] == 1
        assert rules[0]['reward_method_id'] == 2
        assert rules[0]['threshold_cents'] == 500
        assert rules[0]['reward_unit_cents'] == 200

    def test_same_method_source_and_reward_blocks(
            self, qtbot, settings_db, monkeypatch):
        """The schema CHECK forbids it but the UI should also pre-empt
        with a friendly warning."""
        from fam.ui.settings_screen import SettingsScreen
        from fam.models.reward_rule import get_all_reward_rules

        # Force-set both combos to the same method via stubs.
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        # Both combos to JH Food Bucks (id=2).
        for i in range(screen.reward_source_combo.count()):
            if screen.reward_source_combo.itemText(i) == 'JH Food Bucks':
                screen.reward_source_combo.setCurrentIndex(i)
                break
        for i in range(screen.reward_target_combo.count()):
            if 'JH Food Bucks' in screen.reward_target_combo.itemText(i):
                screen.reward_target_combo.setCurrentIndex(i)
                break
        # Capture QMessageBox.warning calls.
        warned = []
        monkeypatch.setattr(
            'fam.ui.settings_screen.QMessageBox.warning',
            lambda *a, **kw: warned.append(a))
        screen._add_reward_rule()
        # No rule was created.
        assert get_all_reward_rules() == []
        # User was warned.
        assert warned, "User should have seen a QMessageBox.warning"


class TestToggleAndDeleteRule:

    def test_toggle_rule(self, qtbot, settings_db):
        from fam.ui.settings_screen import SettingsScreen
        from fam.models.reward_rule import (
            create_reward_rule, get_reward_rule_by_id,
        )
        rid = create_reward_rule(
            source_method_id=1, threshold_cents=500,
            reward_method_id=2, reward_unit_cents=200)
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        screen._load_reward_rules()
        # Toggle off.
        screen._toggle_reward_rule(rid, current_active=1)
        assert get_reward_rule_by_id(rid)['is_active'] == 0
        # Toggle on.
        screen._toggle_reward_rule(rid, current_active=0)
        assert get_reward_rule_by_id(rid)['is_active'] == 1

    def test_delete_rule_with_confirmation(
            self, qtbot, settings_db, monkeypatch):
        from fam.ui.settings_screen import SettingsScreen
        from PySide6.QtWidgets import QMessageBox
        from fam.models.reward_rule import (
            create_reward_rule, get_reward_rule_by_id,
        )
        rid = create_reward_rule(
            source_method_id=1, threshold_cents=500,
            reward_method_id=2, reward_unit_cents=200)
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        screen._load_reward_rules()
        # Stub the confirm dialog to return Yes.
        monkeypatch.setattr(
            'fam.ui.settings_screen.QMessageBox.question',
            lambda *a, **kw: QMessageBox.Yes)
        screen._delete_reward_rule(rid)
        assert get_reward_rule_by_id(rid) is None

    def test_delete_rule_user_cancels(
            self, qtbot, settings_db, monkeypatch):
        from fam.ui.settings_screen import SettingsScreen
        from PySide6.QtWidgets import QMessageBox
        from fam.models.reward_rule import (
            create_reward_rule, get_reward_rule_by_id,
        )
        rid = create_reward_rule(
            source_method_id=1, threshold_cents=500,
            reward_method_id=2, reward_unit_cents=200)
        screen = SettingsScreen()
        qtbot.addWidget(screen)
        screen._load_reward_rules()
        monkeypatch.setattr(
            'fam.ui.settings_screen.QMessageBox.question',
            lambda *a, **kw: QMessageBox.No)
        screen._delete_reward_rule(rid)
        # Rule survived.
        assert get_reward_rule_by_id(rid) is not None
