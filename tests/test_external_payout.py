"""External payout formula unit tests (v2.1.0 / ENH-002, matrix row 1).

Pins the THREE-method money table validated onsite 2026-06-11:

    | Method     | match% | cashes original | FAM owes ($10 face) |
    |------------|--------|-----------------|---------------------|
    | FMNP       | 100%   | YES             | $10 (the MATCH)     |
    | Food RX    | 100%   | no              | $20 (face + match)  |
    | Food Bucks | 0%     | no              | $10 (face only)     |

plus the rounding contract (single rounding step, half AWAY from
zero — verified against a Decimal oracle) and the identity that
makes the generalization safe: FMNP at 100% match with
cashes-original yields exactly face_cents, i.e. today's behavior.
"""

from decimal import Decimal, ROUND_HALF_UP

import pytest

from fam.utils.external_payout import (
    HARD, SOFT,
    compute_external_payout_cents,
    format_match_multiplier,
    lint_external_config,
    match_component_cents,
    reimbursement_basis,
)


# ──────────────────────────────────────────────────────────────────
# Match component + rounding
# ──────────────────────────────────────────────────────────────────


class TestMatchComponent:

    @pytest.mark.parametrize("face,pct,expected", [
        (1000, 100.0, 1000),   # $10 @ 100% -> $10
        (1000, 50.0, 500),     # $10 @ 50%  -> $5
        (1000, 0.0, 0),        # $10 @ 0%   -> $0
        (1000, 200.0, 2000),   # $10 @ 200% -> $20
        (500, 100.0, 500),     # $5 @ 100%  -> $5
        (1, 50.0, 1),          # 0.5c rounds half-away UP to 1c
        (3, 50.0, 2),          # 1.5c -> 2c (banker's would give 2 here,
                               # but 0.5c -> 1c above is the tell)
        (333, 33.5, 112),      # 111.555c -> 112c
    ])
    def test_worked_examples(self, face, pct, expected):
        assert match_component_cents(face, pct) == expected

    def test_half_away_not_bankers(self):
        """Python round() (banker's) would give 0 for 0.5c; the
        money contract is half AWAY from zero -> 1c."""
        assert match_component_cents(1, 50.0) == 1
        # 2.5c -> 3c (banker's would round to 2)
        assert match_component_cents(5, 50.0) == 3

    @pytest.mark.parametrize("pct", [0.0, 25.0, 33.5, 50.0, 100.0,
                                     150.0, 200.0, 999.0])
    def test_decimal_oracle_sweep(self, pct):
        """Integer-arithmetic result == Decimal ROUND_HALF_UP oracle
        across a face-value sweep (non-negative inputs, so half-up
        and half-away coincide)."""
        for face in range(0, 2003):
            oracle = int(
                (Decimal(face) * Decimal(str(pct)) / 100)
                .quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            assert match_component_cents(face, pct) == oracle, (
                f"face={face} pct={pct}")


# ──────────────────────────────────────────────────────────────────
# Payout — the three-method money table
# ──────────────────────────────────────────────────────────────────


class TestComputeExternalPayout:

    def test_fmnp_match_only(self):
        """FMNP: vendor cashes the original; FAM owes the MATCH
        (numerically face at 100%, but it IS the match)."""
        assert compute_external_payout_cents(1000, 100.0, True) == 1000

    def test_food_rx_face_plus_match(self):
        assert compute_external_payout_cents(1000, 100.0, False) == 2000

    def test_food_bucks_face_only(self):
        assert compute_external_payout_cents(1000, 0.0, False) == 1000

    def test_hypothetical_50pct_fmnp(self):
        """The semantics clarification (2026-06-10): at 50% FMNP
        match, FAM owes $5 on a $10 check — the payment is the
        match, not the face."""
        assert compute_external_payout_cents(1000, 50.0, True) == 500

    def test_identity_fmnp_100_cashes_equals_face(self):
        """Golden identity: 100% + cashes-original == face_cents
        EXACTLY, for every face value.  This is what makes the
        generalization a no-op for existing FMNP data."""
        for face in range(1, 100_001, 7):
            assert compute_external_payout_cents(
                face, 100.0, True) == face

    def test_zero_match_cashes_original_owes_zero(self):
        """The G4-SOFT misconfiguration: FAM owes $0."""
        assert compute_external_payout_cents(1000, 0.0, True) == 0


# ──────────────────────────────────────────────────────────────────
# Reimbursement Basis wording (load-bearing for finance audit)
# ──────────────────────────────────────────────────────────────────


class TestReimbursementBasis:

    def test_match_only(self):
        assert reimbursement_basis(1000, 100.0, True) == (
            "Match only ($10.00 × 100%)")

    def test_face_plus_match(self):
        assert reimbursement_basis(1000, 100.0, False) == (
            "Face + match ($10.00 × 2.0)")

    def test_face_only(self):
        assert reimbursement_basis(1000, 0.0, False) == "Face only"

    def test_match_only_non_integer_pct(self):
        assert reimbursement_basis(1000, 50.0, True) == (
            "Match only ($10.00 × 50%)")

    @pytest.mark.parametrize("pct,expected", [
        (100.0, "2.0"),
        (50.0, "1.5"),
        (25.0, "1.25"),
        (0.0, "1.0"),
    ])
    def test_multiplier_format(self, pct, expected):
        assert format_match_multiplier(pct) == expected


# ──────────────────────────────────────────────────────────────────
# G4 config linter
# ──────────────────────────────────────────────────────────────────


def _method(**overrides):
    base = {
        'id': 1, 'name': 'Food RX', 'match_percent': 100.0,
        'is_active': 1, 'sort_order': 3, 'denomination': 1000,
        'photo_required': None, 'is_system': 0,
        'external_matching_accepted': 1, 'vendor_cashes_original': 0,
    }
    base.update(overrides)
    return base


class TestLintExternalConfig:

    def test_clean_config_no_findings(self):
        assert lint_external_config(_method()) == []

    def test_not_external_enabled_is_always_clean(self):
        """The linter only polices the external channel — a booth-
        only method without denomination is fine (SNAP, Cash)."""
        assert lint_external_config(_method(
            external_matching_accepted=0, denomination=None)) == []

    def test_external_without_denomination_is_hard(self):
        findings = lint_external_config(_method(denomination=None))
        assert len(findings) == 1
        assert findings[0][0] == HARD
        assert 'denomination' in findings[0][1]

    def test_cashes_original_zero_match_is_soft(self):
        findings = lint_external_config(_method(
            vendor_cashes_original=1, match_percent=0.0))
        assert len(findings) == 1
        assert findings[0][0] == SOFT
        assert '$0.00' in findings[0][1]

    def test_both_findings_stack(self):
        findings = lint_external_config(_method(
            denomination=None, vendor_cashes_original=1,
            match_percent=0))
        assert {f[0] for f in findings} == {HARD, SOFT}

    def test_fmnp_default_config_is_clean(self):
        assert lint_external_config(_method(
            name='FMNP', denomination=500,
            vendor_cashes_original=1, match_percent=100.0)) == []
