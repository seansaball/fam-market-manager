# FAM Market Manager — Session Rules

Production desktop app (PySide6 / SQLite) that processes **real money** at
farmers markets: SNAP, FMNP, Food RX, Food Bucks, and FAM match dollars.
Vendors get reimbursed from these records. Treat every change as a change
to financial software in active field use.

This file is auto-loaded every session. The deep specification lives in
`PROJECT_INSTRUCTIONS.md` — read the sections relevant to your task
(see "Deep references" below). This file is the protocol; that file is
the encyclopedia.

## Hard rules

1. **Never claim a feature exists or is missing without reading the code.**
   Cite file:line for every such claim — in recommendations, reviews,
   emails, and docs, not just code changes. Prior-art checklist before
   proposing anything "new": `fam/help/content.py` (75+ help articles ≈
   feature inventory), `fam/utils/app_settings.py` (every setting is a
   configurable feature), `tests/` filenames, and the Settings screen tabs.
   This rule exists because two "new feature" recommendations turned out
   to already be built.

2. **All money is integer cents.** Never accumulate currency in floats —
   `0.1 + 0.2 != 0.3` has caused real penny-reconciliation bugs here
   (see the v1.9.10 comment blocks in `fam/sync/data_collector.py`).
   Convert to dollars exactly once, at display/output time.

3. **Never commit, push, tag, or create releases without explicit user
   approval in this session.** Approval for one commit does not carry to
   the next. Release-notes HTML files and `BUGS_BACKLOG.md` are local-only
   by design (gitignored) — do not "helpfully" publish them.

4. **Run the relevant tests before declaring any change done**, and the
   full suite before any release work. Release gate (mandatory before
   tagging): `scripts\run_release_audit.bat`. Known state: 3 fuzz seeds
   and the standalone fuzz simulator (gate 4 of the audit gate) are
   expected-red until BUG-006 closes — see BUGS_BACKLOG.md; CI
   deselects/soft-fails exactly those, annotated with the bug ID.
   The historical Qt/Windows pytest-teardown heap crash was root-caused
   and fixed in commit e2ec756 (`_check_stale_market_days` bails on
   hidden windows); if a teardown crash reappears, start there.

5. **Check `BUGS_BACKLOG.md`** (local, gitignored, project root) before
   starting fix work — it is the running list of known bugs and planned
   enhancements with reproduction details and design notes.

6. **Version source of truth:** `fam/__init__.py`. Schema version:
   `CURRENT_SCHEMA_VERSION` in `fam/database/schema.py` — forward-only,
   additive migrations, each preceded by an automatic backup.

## Change-impact protocol (read this twice)

The single biggest historical failure mode in this codebase is a small,
locally-correct change that breaks something three layers away. One
engine tweak has previously produced a dozen downstream issues. Before
changing anything, map the blast radius:

1. **Grep every call site and consumer** of what you're touching — not
   just the function, but the *fields* it reads and writes. A new field
   on a payment line item must propagate through ALL of: `PaymentRow.
   get_data()` → every entries-builder in `fam/ui/payment_screen.py`
   (there are several) → the engine (`fam/utils/calculations.py`) → the
   save path → `AdjustmentDialog` (parity required) → the fuzz-test
   audit harness (`tests/_coherence.py` + both fuzz suites build their
   own engine calls and must pass the same fields). The `user_capped`
   flag broke fuzz tests in exactly this way.

2. **Check which tests pin the current behavior.** Several tests exist
   specifically to catch identity/shape drift (e.g.
   `tests/test_sync_invariant_matrix.py` pins `SHEET_KEYS`). A pinned
   test failing on your change is a design question, not an obstacle —
   understand why the pin exists before updating it.

3. **Money math changes:** read `docs/SYSTEM_INVARIANTS.md` (U/F/L/R
   invariants) and `docs/FINANCIAL_FORMULA.md` first. The match formula
   intentionally lives in multiple locations that must stay in sync —
   see PROJECT_INSTRUCTIONS §3. The engine's pass ordering in
   `calculate_payment_breakdown` / `resolve_payment_state` /
   `apply_denomination_forfeit` (cap → forfeit Phase A/B → penny
   reconciliation → give-back → denomination snap) is intentional;
   reordering passes breaks invariants in non-obvious ways. Every
   weird-looking branch has a dated comment naming the onsite incident
   that created it — read the comment before "simplifying."

4. **Three-way reconciliation is a hard invariant:** Database = ledger
   backup = Google Sheets sync output. A change to any one layer
   (transaction save, report query, sync collector) usually requires
   matching changes in the other two plus their tests. Reports and sync
   collectors read the same line-item fields the engine writes.

5. **Sheets schema changes** (columns, key shapes): the upsert identity
   lives in `SyncManager.SHEET_KEYS` (`fam/sync/manager.py`). Changing a
   tab's columns or keys interacts with `delete_stale` semantics in
   `fam/sync/gsheets.py` — rows whose keys no longer match are silently
   deleted per-device on the next sync. v2.0.9 shipped docs that got
   this behavior wrong; trace the actual upsert path before documenting
   migration behavior.

6. **Status filtering:** use `ACTIVE_TX_STATUSES` /
   `active_tx_status_clause` from `fam/models/transaction.py:35` — never
   ad-hoc `status IN (...)` literals.

7. **DB triggers enforce invariants at write time** (per-line
   customer+match=method, Voided-is-terminal, Unallocated-Funds
   zero-amount). If your change makes a legitimate write start failing,
   the trigger is probably right and the change is wrong.

8. **UI changes ripple into docs:** screen/workflow changes require
   matching updates to the in-app help (`fam/help/content.py`, guarded
   by `tests/test_help_content.py`), the tutorial overlay
   (`fam/ui/tutorial_overlay.py`, 11 steps), and the printable docs
   (`docs/USER_GUIDE.md`, `docs/QUICK_REFERENCE.md`,
   `docs/COORDINATOR_HANDBOOK.md`). "Code done, docs stale" is a
   recurring review finding.

9. **State your blast-radius map before implementing.** For any
   non-trivial change, list: files touched, files *consuming* what those
   files produce, tests that pin current behavior, and docs that
   describe it. If the list surprises you, the design isn't done.

## Known traps (each cost real debugging time)

- **Denominated vs non-denominated methods take different engine paths.**
  Denominated rows (fixed token face values, `bound_vendor_id`) are
  allocation-exact; non-denominated rows split proportionally across
  vendors — currently *ignoring per-vendor eligibility* (BUG-001 in
  `BUGS_BACKLOG.md`, `fam/ui/payment_screen.py` Phase 2 of
  `_compute_per_vendor_state`). A method without a denomination set
  behaves completely differently from one with it.
- **Reward-type scrip (Food Bucks) must not be matched again** — the
  match was applied when the scrip was earned. Fresh installs seed
  JH Food Bucks at 0% match (ENH-001, fixed in v2.1.0); EXISTING
  markets are not auto-changed and must verify the setting. One
  match % drives both booth AND external math by design.
- **Adjustments are deliberately gated, not unified.** AdjustmentDialog
  shares the engine but not the UI with PaymentScreen; denominated
  transactions route to Void-Instead by design. Keep engine parity
  (same fields, same calls); do not attempt to merge the UIs.
- **`device_id` and `market_code` are identity columns on every synced
  row.** Empty or duplicated device IDs corrupt cross-device sync; the
  app hard-fails launch on missing MachineGuid for this reason.
- **Closed market days accept external payment entries (FMNP, Food
  RX, Food Bucks — the External Payments Entry screen) and admin
  adjust/void, but never new transactions.** That asymmetry is
  intentional.
- **External payout = the entry's SNAPSHOT config, never current
  settings — and never stored.** Always derive via
  `fam/utils/external_payout.py` from `match_percent_snapshot` +
  `vendor_cashes_original_snapshot` (EP1, docs/SYSTEM_INVARIANTS.md
  Layer 10). A DB trigger makes the snapshots immutable; wrong-config
  entries are fixed by void + re-enter, never by editing.
- **External matching adds ZERO new money fields.** Match % and
  denomination are properties of the payment method and drive both
  the booth and external channels; the only external-specific
  settings are the two toggles (`external_matching_accepted`,
  `vendor_cashes_original`). Display is by name snapshot, never a
  live join.
- **The `FMNP Entries` sheet tab is deprecated and DRAINING (R1,
  2026-06-11)** — its collector returns `[]` on purpose so the empty
  upsert removes this device's rows; ALL entries (FMNP included)
  live on `External Payment Entries`. Do NOT "fix" the empty
  collector, and do NOT unregister the tab from SHEET_KEYS /
  REQUIRED_SYNC_TABS until the fleet is uniform and the coordinator
  has deleted the tab — unregistering early stops the drain and
  strands stale rows. The Vendor Reimbursement `FMNP (External)`
  column is NOT part of this deprecation; its feed is unchanged.

## Deep references (read when relevant)

- `PROJECT_INSTRUCTIONS.md` — full spec: §0 handoff state, §3 match
  formula, §4 schema, §8 screens, §8a help-content discipline, §9 tests,
  §10 build/deploy
- `docs/SYSTEM_INVARIANTS.md` — the U/F/L/R invariant catalog
- `docs/FINANCIAL_FORMULA.md` — money math contract
- `BUGS_BACKLOG.md` — known bugs + planned enhancements (local only)
- `RELEASE_NOTES_v*.md` — what shipped when, and why
