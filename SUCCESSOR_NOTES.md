# Successor Notes — carrying FAM Market Manager forward

For the team evolving this desktop app toward a payment-processing
web platform with vendor/customer mobile apps. Written June 2026 at
v2.0.9 by the original builder + AI pair.

## What this codebase actually is

A production PySide6/SQLite desktop app run at real farmers markets
since early 2026. It records SNAP/FMNP/Food RX/Food Bucks
transactions, applies FAM's matching-dollars program rules, syncs
multi-laptop fleets to shared Google Sheets, and produces the
vendor reimbursement numbers checks are cut from. One season of
field hardening; 3,600+ tests; 18 releases.

## What to keep (the portable core)

1. **The money engine** — `fam/utils/calculations.py`. Integer
   cents everywhere, match-cap handling, two-phase denomination
   forfeit, penny reconciliation. Every odd-looking branch has a
   dated comment naming the field incident that created it. This
   IS the domain knowledge.
2. **The business rules** — `docs/FINANCIAL_FORMULA.md` (the math
   contract) and `docs/SYSTEM_INVARIANTS.md` (U/F/L/R invariant
   catalog, enforced by `tests/_coherence.py` auditors). Port these
   before porting any code.
3. **The schema** — `fam/database/schema.py`: 37 versions of
   forward-only migrations encode how the data model evolved and
   why. The snapshot discipline (line items freeze method name and
   match % at confirm time) matters for any system where settings
   change while history must not.
4. **The test suite as a spec** — 161 files describe expected
   behavior in executable form: cap math, multi-vendor allocation,
   void/adjust semantics, sync identity, three-way reconciliation
   (DB == ledger backup == Sheets). Reimplementations should pass
   equivalent tests.
5. **The help corpus** — `fam/help/content.py` (75+ articles) and
   `docs/USER_GUIDE.md` / `COORDINATOR_HANDBOOK.md`: the operational
   reality of market day, written for the volunteers who live it.

## What is platform-bound (rewrite, don't port)

- All of `fam/ui/` (PySide6 widgets/screens), PyInstaller packaging,
  the Google Sheets sync layer (an artifact of "no server" — a real
  backend replaces it), the single-instance lock and device-identity
  machinery (multi-device coordination without a server).

## Concepts that must survive the evolution

- **Per-customer daily match cap** — enforced at entry today;
  becomes account-level logic with reloadable cards.
- **Denominated vs non-denominated instruments** — physical tokens
  with face values vs free-amount tenders take different code paths
  everywhere (allocation, forfeit, reporting). Cards/QR redemption
  inherit this distinction.
- **Vendor eligibility per method** — and see KNOWN_ISSUES.md KI-1
  for the known gap.
- **External (off-booth) collection** — KI-3 in KNOWN_ISSUES.md is
  a fully-specified feature with coordinator-confirmed money flows;
  in a vendor mobile app it likely becomes vendor self-service
  redemption, but the reimbursement math table is the requirement.
- **Audit-first finance** — snapshots, append-only logs, voids over
  edits, plain-English reimbursement bases. Coordinators and a
  finance team rely on every row explaining itself.

## Orientation order

1. `README.md` → `PROJECT_INSTRUCTIONS.md` (full spec; §0 is state)
2. `docs/FINANCIAL_FORMULA.md` + `docs/SYSTEM_INVARIANTS.md`
3. `KNOWN_ISSUES.md` (this repo's honest open-items list)
4. Run it: `pip install -r requirements.txt`, `python run.py`,
   take the in-app tutorial with sample data
5. `python -m pytest` (see CLAUDE.md for the known-red fuzz seeds)

## Working agreements that kept this stable

`CLAUDE.md` captures the development discipline (verify-before-
claiming, integer cents, blast-radius mapping, frozen sheet tabs
for mixed-version fleets). CI (`.github/workflows/tests.yml`) runs
the suite + simulation gates on every push. `scripts\
run_release_audit.bat` is the pre-release gate. These practices —
more than any single design decision — are why one volunteer-run
nonprofit app reached production quality.
