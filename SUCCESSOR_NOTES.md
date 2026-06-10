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

## FUTURE-STATE VISION — the requirements for what you're building

The target: evolve this desktop tool into a cloud-hosted platform —
web front end, vendor mobile app, customer accounts, reloadable FAM
cards. These are the owner's requirements (June 2026) for the
prototype.

### Platform foundation
- Cloud backend with a central database replaces SQLite +
  Google Sheets sync (device IDs, instance locks, and per-laptop
  reconciliation all disappear)
- Web front end replaces the desktop app: any browser is a booth
  station — no installs, no signed binaries, instant releases
- Multi-market from one deployment, role-based access (volunteer,
  coordinator, finance, board)
- Port the proven business core: integer-cents match engine, daily
  cap rules, denominated-instrument logic, audit-first design
  (snapshots, voids-over-edits, plain-English reimbursement bases)

### Customer accounts & reloadable FAM cards — DEFERRED-MATCH MODEL (critical requirement)
- Customers still load funds AT THE FAM BOOTH, **face value only**:
  load $20 SNAP → card shows **$20 SNAP**. No matched dollars are
  created, owed, or floated at load time.
- The account DISPLAYS eligible purchasing power — "$20 loaded → up
  to $40 at the market" per the method's match % — clearly an
  entitlement/discount, NOT a balance.
- **Match materializes only at vendor redemption**: vendor rings
  $10 of goods → card draws $5 face + FAM matches $5 at that
  moment. FAM's match expense is recognized per transaction, when
  the benefit is delivered.
- Why: FAM never carries unredeemed matched-fund liability on its
  books, and nothing matched ever needs to expire — unspent
  balances are purely the customer's own loaded funds.
- Daily match caps enforce at REDEMPTION time (loads can be spent
  across days; the cap applies when matches actually occur).
  Displayed buying power near the cap is an estimate — carry
  forward the honest-disclosure UX of today's confirmation dialog.
- Per-method balances stay distinct (SNAP / Food RX / Food Bucks),
  each with its own match rate and vendor-eligibility rules.
- NOTE: this is exactly how the current engine already thinks —
  `customer_charged` + `match_amount` are computed at the moment of
  sale; match has never pre-existed a transaction in this system.
  The card model splits one booth moment into load-now/redeem-later
  while keeping the match event at redemption.  `calculations.py`'s
  cap-aware logic ports conceptually unchanged.

### Vendor mobile app
- Vendors redeem at their stall: scan customer card/QR, enter
  amount — the FAM booth checkout bottleneck disappears
- Per-vendor method eligibility enforced at redemption (closes
  KNOWN_ISSUES KI-1 by design)
- Today's end-of-market paper-scrip collection (KI-3, External
  Payments) becomes instant digital redemption with the same
  reimbursement math: face value vs face-plus-match per method
- Vendors see their own running reimbursement totals

### Finance & program operations
- Continuous vendor reimbursement totals; one check/ACH per vendor;
  every line item self-explanatory for auditors
- Real-time dashboards: match dollars deployed, budget burn-down,
  per-market and per-zip impact
- Funder reporting (Food Trust, FMNP program) generated from the
  same ledger
- The 3,600-test suite is the executable regression spec — the new
  platform must produce the same answers the field-proven engine
  does

### What carries forward unchanged
- Program rules: match percentages, daily caps, denominated vs
  open-amount instruments, per-method/per-vendor configuration
  (`docs/FINANCIAL_FORMULA.md`, `docs/SYSTEM_INVARIANTS.md`)
- The operational knowledge in the help corpus and coordinator
  handbook — how market day actually works
- The audit-first philosophy that earned coordinator and board
  trust

## Working agreements that kept this stable

`CLAUDE.md` captures the development discipline (verify-before-
claiming, integer cents, blast-radius mapping, frozen sheet tabs
for mixed-version fleets). CI (`.github/workflows/tests.yml`) runs
the suite + simulation gates on every push. `scripts\
run_release_audit.bat` is the pre-release gate. These practices —
more than any single design decision — are why one volunteer-run
nonprofit app reached production quality.
