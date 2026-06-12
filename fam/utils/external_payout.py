"""External-payment payout math (v2.1.0 / ENH-002).

THE single home for the external payout formula and the
plain-English "Reimbursement Basis" wording.  Every surface that
shows or sums an external payout — entry-screen preview, save-time
confirmation dialog, sheet collectors, reports screen, CSV export,
ledger backup, coherence auditors — derives it by calling into this
module with the ENTRY'S SNAPSHOT config, never the method's current
settings.  Payout is never stored in the database, so there is no
second copy to drift (invariant EP1, docs/SYSTEM_INVARIANTS.md).

The formula (docs/FINANCIAL_FORMULA.md "External payout"):

    vendor_owed_cents = round_half_away(face_cents x match% / 100)
                      + (0 if vendor_cashes_original else face_cents)

* ``vendor_cashes_original`` ON means the vendor retains and cashes
  the physical instrument with the issuing program (the FMNP model),
  so FAM owes the MATCH component only.  At FMNP's 100% match the
  payout numerically equals face value — but it IS the match, not
  the face; the basis wording keeps that distinction so non-100%
  configurations stay honest (a 50% FMNP match would owe $5 on a
  $10 check).
* One rounding step, at the end of the match component, ROUND HALF
  AWAY FROM ZERO.  Implemented in pure integer arithmetic — Python's
  ``round()`` is banker's rounding and floats drift; neither is
  acceptable for money (CLAUDE.md rule 2).

All amounts are integer cents.  Match percent is the REAL stored on
``payment_methods.match_percent`` (0–999, enforced by the
``chk_match_percent_*`` triggers).
"""

import logging

logger = logging.getLogger('fam.utils.external_payout')


def match_component_cents(face_cents: int, match_percent: float) -> int:
    """Return the match component of an external payout, in cents.

    ``round_half_away(face_cents x match_percent / 100)`` computed
    exactly: the percent is scaled to integer basis points (100.0%
    -> 10000 bp; the chk_match_percent triggers cap the column at
    999 with at most UI-entered decimals, so ``round()`` here only
    cleans float representation noise, e.g. 50.5 * 100 ->
    5049.999... -> 5050), then a single integer division performs
    the one rounding step, half away from zero.  Inputs are
    non-negative (face > 0 enforced by chk_fmnp_amount_*;
    match_percent >= 0 by chk_match_percent_*).
    """
    basis_points = round(match_percent * 100)
    return (face_cents * basis_points + 5000) // 10000


def compute_external_payout_cents(face_cents: int, match_percent: float,
                                  vendor_cashes_original: bool) -> int:
    """Return what FAM owes the vendor for an external entry, in cents.

    Callers MUST pass the entry's snapshot config
    (``match_percent_snapshot``, ``vendor_cashes_original_snapshot``)
    for saved entries — never the method's current settings.  For
    not-yet-saved previews (entry screen, settings dialog), the
    method's current values are correct because they are exactly
    what will be snapshotted at save time.

    Identity (golden-pinned): at 100% match with
    vendor_cashes_original=True this returns exactly ``face_cents``
    — today's FMNP (External) behavior, preserved byte-for-byte.
    """
    match_cents = match_component_cents(face_cents, match_percent)
    if vendor_cashes_original:
        return match_cents
    return match_cents + face_cents


def format_match_multiplier(match_percent: float) -> str:
    """Render the face+match multiplier, e.g. 100.0 -> "2.0"."""
    mult = 1.0 + match_percent / 100.0
    # Trim float noise: "2.0", "1.5", "1.25" — two decimals max,
    # no trailing zero beyond the first decimal place.
    text = f"{mult:.2f}".rstrip('0')
    if text.endswith('.'):
        text += '0'
    return text


def reimbursement_basis(face_cents: int, match_percent: float,
                        vendor_cashes_original: bool) -> str:
    """Plain-English basis string for reports, sheets, and previews.

    The wording is load-bearing for the finance team: every synced
    row must be auditable in isolation, including spotting entries
    made under misconfigured settings (the basis text won't match
    the method's expected pattern).  Three shapes:

      * "Match only ($10.00 x 100%)"   — vendor cashes the original
      * "Face + match ($10.00 x 2.0)"  — FAM owes face plus match
      * "Face only"                    — no match (e.g. Food Bucks)
    """
    from fam.utils.money import format_dollars

    face_str = format_dollars(face_cents)
    if vendor_cashes_original:
        pct = (f"{match_percent:g}" if match_percent == int(match_percent)
               else f"{match_percent}")
        return f"Match only ({face_str} × {pct}%)"
    if match_percent > 0:
        return (f"Face + match ({face_str} × "
                f"{format_match_multiplier(match_percent)})")
    return "Face only"


# ── G4 config linter ─────────────────────────────────────────────

# Finding severities.  HARD findings block the save (Settings dialog
# refuses OK; the model layer raises on the .fam-import path); SOFT
# findings are surfaced as a confirm-anyway warning.
HARD = 'hard'
SOFT = 'soft'


def lint_external_config(method: dict) -> list[tuple[str, str]]:
    """Validate a payment method's external-matching configuration.

    Returns ``[(severity, message), ...]`` — empty when clean.
    *method* is a payment_methods row dict reflecting the PROPOSED
    state (callers apply pending edits before linting).

    Rules (ENH-002 G4):
      * HARD — external-enabled with no denomination.  Paper scrip
        always has a face value; the denomination drives the
        whole-multiple guard (G2) and the instrument count.
        (Pre-v38 FMNP configs without a denomination are
        grandfathered at the entry screen, but Settings refuses to
        SAVE such a state going forward.)
      * SOFT — vendor-cashes-original with 0% match: FAM would owe
        $0 for every entry.  Almost certainly a misconfiguration.
    """
    findings: list[tuple[str, str]] = []
    if not method.get('external_matching_accepted'):
        return findings

    denomination = method.get('denomination')
    if not denomination or denomination <= 0:
        findings.append((
            HARD,
            f"\"{method.get('name', '?')}\" cannot accept external "
            f"matching without a denomination. Paper scrip always "
            f"has a face value — set the denomination first (it "
            f"drives the whole-multiple check and instrument "
            f"count)."))

    if (method.get('vendor_cashes_original')
            and not method.get('match_percent')):
        findings.append((
            SOFT,
            f"\"{method.get('name', '?')}\" is set so the vendor "
            f"cashes the original instrument AND match is 0% — FAM "
            f"would owe the vendor $0.00 for every entry. This is "
            f"almost certainly a misconfiguration."))

    return findings
