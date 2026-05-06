"""Tests for the PaymentConfirmationDialog's rewards zone.

The rewards zone is purely informational — it tells the cashier to
hand the customer physical scrip tokens at confirmation time.  It
does NOT participate in the financial action zone (no totals
affected, no checkbox required, no denomination-overage interaction).

Pinned guarantees:
  1. ``reward_lines=None`` or ``[]`` → no rewards zone in the dialog.
  2. ``reward_lines=[...]`` → zone visible with the right rows.
  3. The dialog's ``customer_total`` and ``match_total`` are
     unaffected by the presence/absence of rewards (the action zone
     stays a pure financial view).
  4. The Confirm button's enabled-state is unaffected by rewards
     (only external-device checkboxes gate Confirm).
  5. The disclaimer language is present so a future reader can't
     confuse the rewards section with the financial flow.
"""

import pytest

from fam.utils.rewards import RewardLine


def _basic_line_items():
    """One-method, no-overage payment fixture used across the
    tests below.  $5 SNAP customer + $5 match = $10 method."""
    return [
        {'method_amount': 1000, 'customer_charged': 500,
         'match_amount': 500},
    ]


def _basic_items():
    return [
        {'payment_method_id': 1, 'method_name_snapshot': 'SNAP',
         'denomination': None, 'customer_charged': 500,
         'method_amount': 1000, 'match_amount': 500},
    ]


def _sample_reward_line():
    """Canonical reward: 1 × $2 JH Food Bucks earned from $5 SNAP."""
    return RewardLine(
        rule_id=1,
        source_method_id=1,
        source_method_name='SNAP',
        source_total_cents=500,
        threshold_cents=500,
        reward_method_id=2,
        reward_method_name='JH Food Bucks',
        reward_unit_cents=200,
        n_units=1,
        reward_total_cents=200,
    )


class TestRewardsZoneVisibility:

    def test_no_rewards_zone_when_argument_omitted(self, qtbot):
        """Default arg ``reward_lines=None`` → no rewards zone
        — backward-compatible with v1.9.9 callers."""
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        dlg = PaymentConfirmationDialog(
            line_items=_basic_line_items(),
            items=_basic_items(),
            receipt_total=1000,
            denom_overage=0,
            receipt_count=1,
        )
        qtbot.addWidget(dlg)
        # Find any QFrame with objectName 'rewardsZone' — must be absent.
        from PySide6.QtWidgets import QFrame
        zones = [c for c in dlg.findChildren(QFrame)
                 if c.objectName() == 'rewardsZone']
        assert len(zones) == 0, (
            f"Expected no rewards zone when reward_lines is None; "
            f"found {len(zones)}")

    def test_no_rewards_zone_when_list_empty(self, qtbot):
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        dlg = PaymentConfirmationDialog(
            line_items=_basic_line_items(),
            items=_basic_items(),
            receipt_total=1000,
            denom_overage=0,
            receipt_count=1,
            reward_lines=[],
        )
        qtbot.addWidget(dlg)
        from PySide6.QtWidgets import QFrame
        zones = [c for c in dlg.findChildren(QFrame)
                 if c.objectName() == 'rewardsZone']
        assert len(zones) == 0

    def test_rewards_zone_visible_when_lines_provided(self, qtbot):
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        dlg = PaymentConfirmationDialog(
            line_items=_basic_line_items(),
            items=_basic_items(),
            receipt_total=1000,
            denom_overage=0,
            receipt_count=1,
            reward_lines=[_sample_reward_line()],
        )
        qtbot.addWidget(dlg)
        from PySide6.QtWidgets import QFrame
        zones = [c for c in dlg.findChildren(QFrame)
                 if c.objectName() == 'rewardsZone']
        assert len(zones) == 1, (
            "Expected exactly one rewards zone when reward lines "
            "are passed in")


class TestRewardsZoneContent:

    def _zone_text(self, dlg):
        """Concatenate every QLabel text under the rewards zone."""
        from PySide6.QtWidgets import QFrame, QLabel
        zone = next(
            c for c in dlg.findChildren(QFrame)
            if c.objectName() == 'rewardsZone'
        )
        return ' || '.join(
            l.text() for l in zone.findChildren(QLabel))

    def test_reward_amount_displayed(self, qtbot):
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        dlg = PaymentConfirmationDialog(
            line_items=_basic_line_items(),
            items=_basic_items(),
            receipt_total=1000, denom_overage=0, receipt_count=1,
            reward_lines=[_sample_reward_line()],
        )
        qtbot.addWidget(dlg)
        text = self._zone_text(dlg)
        assert 'JH Food Bucks' in text
        assert '$2.00' in text
        assert '1 ×' in text or '1 x' in text or '1 *' in text

    def test_disclaimer_present(self, qtbot):
        """The disclaimer line must be present so a future reader
        can't confuse the rewards section with the financial flow."""
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        dlg = PaymentConfirmationDialog(
            line_items=_basic_line_items(),
            items=_basic_items(),
            receipt_total=1000, denom_overage=0, receipt_count=1,
            reward_lines=[_sample_reward_line()],
        )
        qtbot.addWidget(dlg)
        text = self._zone_text(dlg).lower()
        assert 'not vendor reimbursement' in text or \
               'not part of this payment' in text or \
               'marketing' in text, (
            f"Rewards zone must carry a disclaimer that it's NOT "
            f"part of vendor reimbursement / financial flow.  "
            f"Got: {text!r}")

    def test_multiple_reward_lines_all_rendered(self, qtbot):
        """Two rules firing on one order → two lines in the zone."""
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        line2 = RewardLine(
            rule_id=2, source_method_id=3,
            source_method_name='Cash', source_total_cents=1000,
            threshold_cents=1000, reward_method_id=4,
            reward_method_name='Food RX', reward_unit_cents=1000,
            n_units=1, reward_total_cents=1000)
        dlg = PaymentConfirmationDialog(
            line_items=_basic_line_items(),
            items=_basic_items(),
            receipt_total=1000, denom_overage=0, receipt_count=1,
            reward_lines=[_sample_reward_line(), line2],
        )
        qtbot.addWidget(dlg)
        text = self._zone_text(dlg)
        assert 'JH Food Bucks' in text
        assert 'Food RX' in text


class TestActionZoneUnchangedByRewards:
    """The financial action zone must be IDENTICAL whether rewards
    are present or absent — rewards are a pure overlay."""

    def test_action_zone_total_unchanged_by_rewards(self, qtbot):
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        from PySide6.QtWidgets import QLabel

        # Without rewards.
        dlg_a = PaymentConfirmationDialog(
            line_items=_basic_line_items(),
            items=_basic_items(),
            receipt_total=1000, denom_overage=0, receipt_count=1,
        )
        qtbot.addWidget(dlg_a)
        labels_a = [l.text() for l in dlg_a.findChildren(QLabel)]
        # With rewards.
        dlg_b = PaymentConfirmationDialog(
            line_items=_basic_line_items(),
            items=_basic_items(),
            receipt_total=1000, denom_overage=0, receipt_count=1,
            reward_lines=[_sample_reward_line()],
        )
        qtbot.addWidget(dlg_b)
        labels_b = [l.text() for l in dlg_b.findChildren(QLabel)]
        # The "TOTAL TO COLLECT" label and amount appear in both
        # — rewards don't change what the cashier collects.
        assert 'TOTAL TO COLLECT' in labels_a
        assert 'TOTAL TO COLLECT' in labels_b
        assert '$5.00' in labels_a   # customer total
        assert '$5.00' in labels_b


class TestConfirmEnabledIndependentOfRewards:

    def test_confirm_enabled_with_no_external_device_and_rewards(
            self, qtbot):
        """Rewards alone don't gate Confirm — only external-device
        (SNAP/EBT) checkboxes do.  Cash-only payment + reward →
        Confirm is enabled by default."""
        from fam.ui.widgets.payment_confirmation_dialog import (
            PaymentConfirmationDialog,
        )
        cash_items = [
            {'payment_method_id': 3, 'method_name_snapshot': 'Cash',
             'denomination': None, 'customer_charged': 500,
             'method_amount': 500, 'match_amount': 0},
        ]
        cash_line_items = [
            {'method_amount': 500, 'customer_charged': 500,
             'match_amount': 0},
        ]
        dlg = PaymentConfirmationDialog(
            line_items=cash_line_items,
            items=cash_items,
            receipt_total=500, denom_overage=0, receipt_count=1,
            reward_lines=[_sample_reward_line()],
        )
        qtbot.addWidget(dlg)
        assert dlg._confirm_btn.isEnabled(), (
            "Cash-only payment with rewards present → Confirm "
            "should be enabled (rewards do not gate the button)")
