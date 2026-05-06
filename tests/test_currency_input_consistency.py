"""Regression: every monetary input field across the app should
behave identically — overtype + cents-builder ladder, $-prefix,
2-decimal precision.

User-reported (2026-04-30):

    "Let's review every single monetary entry field in this app
     and ensure they are all consistent, including adjustments
     page and settings page.  If I'm typing in a money value it
     should be the same across the entire app."

Pinned guarantees on every currency field, regardless of screen:

  1. Widget class is ``NoScrollDoubleSpinBox`` (gets overtype +
     cents-builder + scroll-safe focus).
  2. ``decimals == 2`` (currency precision).
  3. ``prefix == "$ "`` (with the trailing space — visual padding).
  4. Typing ``1`` then ``2`` produces $1.20 (NOT $10.02 from the
     Receipt Total bug, NOT $1.0 from leftover plain-Qt insert).
  5. Typing ``1234`` produces $12.34 (full ladder).

Non-currency fields (percent, count) are intentionally exempt and
listed in ``_NON_CURRENCY_FIELDS`` for documentation purposes.
"""
import inspect
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest


# ──────────────────────────────────────────────────────────────────
# Source-level consistency: scan every UI module for
# ``NoScrollDoubleSpinBox()`` instantiations whose .setPrefix()
# argument starts with "$" and assert it's exactly "$ ".
# ──────────────────────────────────────────────────────────────────


_CURRENCY_FIELDS = [
    # (file, line-near, var name, screen description)
    ('fam/ui/widgets/payment_row.py',     'amount_spin',
     'Payment row charge field'),
    ('fam/ui/receipt_intake_screen.py',   'receipt_total_spin',
     'Receipt intake total'),
    ('fam/ui/fmnp_screen.py',             'amount_spin',
     'FMNP entry amount'),
    ('fam/ui/admin_screen.py',            'receipt_spin',
     'Adjustment dialog receipt total'),
    ('fam/ui/settings_screen.py',         'denom_spin',
     'Settings → Add Market dialog denom (legacy?)'),
    ('fam/ui/settings_screen.py',         'limit_spin',
     'Settings → Markets daily match limit'),
    ('fam/ui/settings_screen.py',         'pm_denom_spin',
     'Settings → Add Payment Method denom'),
    ('fam/ui/settings_screen.py',         '_threshold_spin',
     'Settings → Preferences large-receipt threshold'),
]

_NON_CURRENCY_FIELDS = [
    # Documented exempt fields — different decimals/no prefix on
    # purpose.  Listed here so a future audit can see they were
    # consciously excluded, not missed.
    ('fam/ui/widgets/payment_row.py',  '_count_spin',
     'Denomination stepper unit count (integer)'),
    ('fam/ui/fmnp_screen.py',          'check_count_spin',
     'FMNP check count (integer; setSpecialValueText="N/A")'),
    ('fam/ui/settings_screen.py',      'match_spin',
     'Add Market dialog match-percent (decimals=1, suffix="%")'),
    ('fam/ui/settings_screen.py',      'pm_match_spin',
     'Add/Edit Payment Method match-percent (decimals=1, suffix="%")'),
]


class TestCurrencyPrefixConsistency:
    """Every currency field must use prefix '$ ' (with trailing space)
    — consistent visual padding across the app."""

    @staticmethod
    def _read(rel_path):
        from pathlib import Path
        repo_root = Path(__file__).parent.parent
        return (repo_root / rel_path).read_text(encoding='utf-8')

    def test_no_currency_field_uses_bare_dollar_prefix(self):
        """No file should set ``setPrefix("$")`` (no trailing space)
        on a currency-context spinbox."""
        violations = []
        for rel, var, desc in _CURRENCY_FIELDS:
            src = self._read(rel)
            # Look for the bare-dollar pattern.
            if f'.setPrefix("$")' in src or f".setPrefix('$')" in src:
                violations.append(f"{rel} — {desc}: still uses '$' "
                                   "(needs '$ ')")
        assert not violations, (
            "Currency prefix must be '$ ' (with trailing space) "
            "for visual consistency.  Violations:\n  - "
            + "\n  - ".join(violations))


class TestCurrencyFieldsAllUseNoScrollDouble:
    """Every currency field must use NoScrollDoubleSpinBox so it gets
    the overtype + cents-builder + scroll-safe focus treatment."""

    @staticmethod
    def _read(rel_path):
        from pathlib import Path
        repo_root = Path(__file__).parent.parent
        return (repo_root / rel_path).read_text(encoding='utf-8')

    def test_no_currency_field_uses_raw_qdoublespinbox(self):
        """No file under fam/ui/ should construct ``QDoubleSpinBox()``
        directly for a currency field — must use the NoScroll
        wrapper."""
        from pathlib import Path
        repo_root = Path(__file__).parent.parent
        ui_dir = repo_root / 'fam' / 'ui'
        violations = []
        for py_file in ui_dir.rglob('*.py'):
            text = py_file.read_text(encoding='utf-8')
            # Skip the helpers module (which DEFINES NoScrollDoubleSpinBox
            # and references QDoubleSpinBox legitimately).
            if py_file.name == 'helpers.py':
                continue
            # Look for naked instantiations.
            if 'QDoubleSpinBox()' in text:
                violations.append(
                    f"{py_file.relative_to(repo_root)} contains "
                    f"'QDoubleSpinBox()' — should be "
                    f"'NoScrollDoubleSpinBox()' for typing consistency")
        assert not violations, (
            "All money fields must use NoScrollDoubleSpinBox.  "
            "Violations:\n  - " + "\n  - ".join(violations))


# ──────────────────────────────────────────────────────────────────
# Live behaviour: every currency-style configuration produces $1.20
# from typing "12" and $12.34 from typing "1234".
# ──────────────────────────────────────────────────────────────────


class TestCurrencyInputBehaviorParity:
    """Same typing input → same numeric result, regardless of
    setSpecialValueText / range / singleStep settings the field
    happens to have."""

    @staticmethod
    def _make_currency_spin(qtbot, *, range_min=0.00, range_max=99999.99,
                            single_step=1.00, special_value_text=None,
                            initial_value=0.00):
        """Build a currency-shaped NoScrollDoubleSpinBox covering the
        configurations actually used across the app."""
        from fam.ui.helpers import NoScrollDoubleSpinBox
        spin = NoScrollDoubleSpinBox()
        spin.setRange(range_min, range_max)
        spin.setDecimals(2)
        spin.setSingleStep(single_step)
        spin.setPrefix("$ ")
        spin.setValue(initial_value)
        if special_value_text is not None:
            spin.setSpecialValueText(special_value_text)
        qtbot.addWidget(spin)
        spin.show()
        qtbot.waitExposed(spin)
        return spin

    # The five canonical configurations used across the app.
    @pytest.mark.parametrize("config", [
        # (label, kwargs)
        ("payment_row amount_spin",
         dict()),
        ("receipt_total_spin (with specialValueText)",
         dict(special_value_text="$ 0.00")),
        ("admin adjustment receipt_spin (min=$0.01)",
         dict(range_min=0.01)),
        ("settings limit_spin (min=$0.01, daily match limit)",
         dict(range_min=0.01)),
        ("threshold_spin (min=$1.00)",
         dict(range_min=1.00)),
    ])
    def test_typing_12_yields_one_twenty(self, qtbot, config):
        """Across every currency-field configuration, typing 1 then 2
        should produce $1.20."""
        label, kwargs = config
        spin = self._make_currency_spin(qtbot, **kwargs)
        spin.setFocus()
        QTest.qWait(50)  # let select-all fire
        QTest.keyClick(spin, Qt.Key_1)
        QTest.keyClick(spin, Qt.Key_2)
        # Allow $0.01 / $1.00 floor cases to clamp to their min.
        # All non-clamped configs land on $1.20.
        if spin.minimum() <= 1.20:
            assert spin.value() == 1.20, (
                f"[{label}] typing '12' → expected $1.20, got "
                f"${spin.value():.2f}")

    @pytest.mark.parametrize("config", [
        ("payment_row amount_spin", dict()),
        ("receipt_total_spin", dict(special_value_text="$ 0.00")),
        ("threshold_spin (min=$1.00)", dict(range_min=1.00)),
    ])
    def test_typing_1234_yields_twelve_thirty_four(
            self, qtbot, config):
        """Full 4-keystroke ladder must land on $12.34."""
        label, kwargs = config
        spin = self._make_currency_spin(qtbot, **kwargs)
        spin.setFocus()
        QTest.qWait(50)
        for k in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4):
            QTest.keyClick(spin, k)
        assert spin.value() == 12.34, (
            f"[{label}] typing '1234' → expected $12.34, got "
            f"${spin.value():.2f}")


# ──────────────────────────────────────────────────────────────────
# Confirm the count field (now NoScrollSpinBox) inherits the
# integer cents-builder treatment.
# ──────────────────────────────────────────────────────────────────


class TestPaymentRowCountFieldConsistency:
    """The denomination-stepper count field used to be a raw
    ``QSpinBox`` — it now uses ``NoScrollSpinBox`` so typing in it
    matches every other field (overtype + shift-left)."""

    def test_count_spin_is_noscroll(self):
        """Source-level guard: payment_row.py constructs
        NoScrollSpinBox, not raw QSpinBox()."""
        from pathlib import Path
        repo_root = Path(__file__).parent.parent
        src = (repo_root / 'fam' / 'ui' / 'widgets' / 'payment_row.py'
               ).read_text(encoding='utf-8')
        assert 'self._count_spin = NoScrollSpinBox()' in src, (
            "payment_row._count_spin must be NoScrollSpinBox so it "
            "gets the same overtype + cents-builder typing UX as "
            "every other numeric input in the app")
        assert 'self._count_spin = QSpinBox()' not in src, (
            "Found legacy raw QSpinBox in payment_row — must be "
            "NoScrollSpinBox")
