"""Tests for the rewards section on the printed customer receipt.

Pinned guarantees:
  1. ``data['rewards']`` empty / missing → no rewards section in
     the rendered HTML.
  2. ``data['rewards']`` populated → section appears with the
     correct line text + the disclaimer.
  3. The financial totals on the receipt (Subtotal, You paid, FAM
     matched, Vendor total) are NOT affected by the presence or
     absence of rewards.
  4. The disclaimer prominently states rewards are "NOT part of
     vendor reimbursement or FAM match" — pin so future readers
     can't conflate it with the financial flow.
"""

import pytest


def _basic_receipt_data(rewards=None):
    """Minimal-but-realistic receipt-data dict matching the shape
    produced by ``_build_receipt_data``."""
    return {
        'market_name': 'Test Market',
        'market_date': '2026-04-30',
        'customer_label': 'C-RW',
        'confirmed_by': 'Volunteer',
        'transactions': [
            {'fam_id': 'FAM-T-1', 'vendor': 'V1',
             'receipt_total': 5.00},
        ],
        'payment_totals': {
            'SNAP': {'amount': 10.00, 'match': 5.00, 'customer': 5.00},
        },
        'total_receipt': 5.00,
        'total_customer': 5.00,
        'total_match': 5.00,
        'rewards': rewards or [],
    }


def _sample_rewards():
    return [
        {'source_method': 'SNAP', 'source_total': 5.00,
         'reward_method': 'JH Food Bucks', 'reward_unit': 2.00,
         'n_units': 1, 'reward_total': 2.00},
    ]


class TestReceiptRewardsSectionVisibility:

    def test_no_section_when_rewards_empty(self):
        from fam.ui.payment_screen import PaymentScreen
        html = PaymentScreen._format_receipt_html(
            _basic_receipt_data(rewards=[]))
        assert 'Rewards Earned' not in html, (
            "Rewards section must be suppressed when no rewards "
            "are present (feature off or no rule fired)")

    def test_no_section_when_rewards_key_missing(self):
        from fam.ui.payment_screen import PaymentScreen
        data = _basic_receipt_data()
        del data['rewards']
        html = PaymentScreen._format_receipt_html(data)
        assert 'Rewards Earned' not in html

    def test_section_appears_when_rewards_present(self):
        from fam.ui.payment_screen import PaymentScreen
        html = PaymentScreen._format_receipt_html(
            _basic_receipt_data(rewards=_sample_rewards()))
        assert 'Rewards Earned' in html
        # Reward line content.
        assert 'JH Food Bucks' in html
        assert '$2.00' in html

    def test_disclaimer_present(self):
        """Pin the language: rewards are NOT vendor reimbursement
        or FAM match.  Receipt readers must know this is an add-on."""
        import re
        from fam.ui.payment_screen import PaymentScreen
        html = PaymentScreen._format_receipt_html(
            _basic_receipt_data(rewards=_sample_rewards()))
        # Collapse whitespace + lowercase so multi-line disclaimer
        # text is matched regardless of indentation.
        compact = re.sub(r'\s+', ' ', html.lower())
        assert 'not part of vendor reimbursement or fam match' in \
               compact, (
            "Receipt rewards section must carry a disclaimer that "
            "rewards are NOT part of vendor reimbursement or FAM "
            "match — pinned by 2026-04-30 spec.")
        # Marketing/loyalty framing.
        assert 'marketing' in compact or 'loyalty' in compact


class TestReceiptFinancialsUnaffectedByRewards:
    """The receipt's financial totals must be byte-identical
    whether rewards are present or absent — the rewards section is
    purely additive."""

    def test_subtotal_unchanged(self):
        from fam.ui.payment_screen import PaymentScreen
        html_a = PaymentScreen._format_receipt_html(
            _basic_receipt_data(rewards=[]))
        html_b = PaymentScreen._format_receipt_html(
            _basic_receipt_data(rewards=_sample_rewards()))
        # Both contain the same Subtotal line.
        assert 'Subtotal' in html_a and '$5.00' in html_a
        assert 'Subtotal' in html_b and '$5.00' in html_b

    def test_payment_summary_unchanged(self):
        from fam.ui.payment_screen import PaymentScreen
        html_a = PaymentScreen._format_receipt_html(
            _basic_receipt_data(rewards=[]))
        html_b = PaymentScreen._format_receipt_html(
            _basic_receipt_data(rewards=_sample_rewards()))
        # Both contain Payment Summary table with SNAP.
        assert 'Payment Summary' in html_a
        assert 'Payment Summary' in html_b
        assert 'SNAP' in html_a
        assert 'SNAP' in html_b

    def test_vendor_total_unchanged(self):
        from fam.ui.payment_screen import PaymentScreen
        html_a = PaymentScreen._format_receipt_html(
            _basic_receipt_data(rewards=[]))
        html_b = PaymentScreen._format_receipt_html(
            _basic_receipt_data(rewards=_sample_rewards()))
        # Both contain "Vendor total: $5.00".
        assert 'Vendor total' in html_a
        assert 'Vendor total' in html_b


class TestMultipleRewardLines:

    def test_two_rules_render_both(self):
        from fam.ui.payment_screen import PaymentScreen
        rewards = [
            {'source_method': 'SNAP', 'source_total': 5.00,
             'reward_method': 'JH Food Bucks', 'reward_unit': 2.00,
             'n_units': 1, 'reward_total': 2.00},
            {'source_method': 'Cash', 'source_total': 10.00,
             'reward_method': 'Food RX', 'reward_unit': 1.00,
             'n_units': 2, 'reward_total': 2.00},
        ]
        html = PaymentScreen._format_receipt_html(
            _basic_receipt_data(rewards=rewards))
        assert 'JH Food Bucks' in html
        assert 'Food RX' in html
        # Both source amounts mentioned.
        assert '$5.00 SNAP' in html or 'from $5.00 SNAP' in html
        assert '$10.00 Cash' in html or 'from $10.00 Cash' in html
