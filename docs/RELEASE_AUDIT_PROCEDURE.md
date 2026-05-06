# Release Audit Procedure (mandatory gate)

> **Status:** Mandatory. **Every** release of FAM Market Manager — from
> hotfixes to major versions — must pass the full Production Readiness
> Audit before tagging, building, or distributing.
>
> No exceptions for "small fixes," "cosmetic changes," or
> "documentation-only" PRs. The cost of running the audit is ~3 minutes
> of CPU. The cost of a financial-integrity regression at a live market
> is unrecoverable.
>
> **Owner:** Repository maintainer.
> **Last reviewed:** v1.9.9 (April 2026).

---

## 1. The four gates

A release ships **only when all four of these exit cleanly:**

| # | Gate | Command | Pass criterion |
|---|------|---------|----------------|
| 1 | **Pytest suite** | `python -m pytest` | All tests pass (zero `failed`, zero `error`) |
| 2 | **Production simulation** | `python -m scripts.production_sim` | Exit 0; no `[FAIL]` lines |
| 3 | **v1.9.9 stress simulation** | `python -m scripts.v1_9_9_stress_sim` | Exit 0; no `[FAIL]` lines |
| 4 | **Randomized fuzz smoke** | `python -m scripts.fuzz_simulator` | Exit 0; no `Failures` reported (5 seeds × 100 actions) |

Warnings (`[WARN]`) in the simulations are **acceptable** when they
correspond to documented gaps already pinned in
`tests/test_audit_coverage_gaps.py`. Any **new** warning is a release
blocker until investigated.

---

## 2. The one-command runner

For convenience, the project ships `scripts\run_release_audit.bat`:

```bat
scripts\run_release_audit.bat
```

It runs all three gates in sequence, halts on the first failure, and
prints a final pass/fail banner. Use it as the canonical release-gate
invocation; CI and manual runs should both use it.

For non-Windows (CI / WSL) environments use the cross-platform
shorthand:

```bash
python -m pytest -q && \
  python -m scripts.production_sim && \
  python -m scripts.v1_9_9_stress_sim
```

---

## 3. What each gate proves

### 3.1 Pytest suite

* **Count:** 2 090+ tests across 40+ files (as of v1.9.9).
* **Wall time:** ~65 seconds.
* **What it proves:** every documented behavior — formula correctness,
  schema migrations, UI guards, sync, auto-update, photo dedup,
  audit-log coverage, export reconciliation — still holds. The audit
  additions in v1.9.9 (`test_audit_coverage_gaps.py`,
  `test_export_reconciliation.py`, `test_production_stress.py`) are
  part of this gate.

### 3.2 Production simulation (`scripts/production_sim.py`)

* **Count:** ~43 reconciliation invariants over 3 sessions
  (small / medium / heavy) producing 300+ simulated transactions.
* **Wall time:** ~2 seconds.
* **What it proves:**
  * Every per-transaction invariant holds at scale
  * Receipt totals agree with method allocations across the whole DB
  * Vendor reimbursement totals reconcile per vendor
  * FAM Match report total equals the raw DB sum
  * No dangling Draft transactions after market close
  * DB triggers reject negative amounts
  * Abrupt connection close does not lose Draft transactions
  * Reopen + late transaction works end-to-end
  * Backup files are valid SQLite

### 3.3 v1.9.9 stress simulation (`scripts/v1_9_9_stress_sim.py`)

* **Count:** 34 reconciliation invariants in a deliberate "zero
  tolerance" mega-scenario.
* **Wall time:** ~1 second.
* **What it proves:**
  * 10-vendor single-customer mega order reconciles to ±0¢ across
    Vendor Reimbursement, FAM Match, and Detailed Ledger
  * Returning customer cap accumulates across visits and the third
    visit is correctly capped (not silently exceeded)
  * Voiding an earlier visit frees cap for later visits
  * 5-iteration adjustment chain on a single transaction preserves
    the per-transaction invariant after every step
  * Audit chain grows by ≥10 entries across 5 adjustments
  * Adjust-then-void cascades the correct amount out of vendor
    reimbursement
  * Edge cases pass: $0.01 receipts, 200% match, 0% match,
    `match_limit=0`, multi-denomination ($2 + $5 in one txn)
  * All four report surfaces (Vendor Reimbursement, FAM Match,
    Detailed Ledger, Market Day Summary) reconcile with DB ground
    truth
  * Ledger backup file is written and non-empty
  * All seven required audit action codes are present
  * Documented audit gaps (vendors / payment_methods CRUD) are
    *still* gaps (no silent regression of the gap inventory)
  * DB triggers reject `method_amount < 0`, `receipt_total ≤ 0`,
    `match_percent > 999`

---

## 4. When to re-run

| Trigger | Required gates | Notes |
|---------|----------------|-------|
| Bug fix touching `fam/utils/calculations.py`, `fam/models/*.py`, `fam/sync/*.py`, or `fam/utils/export.py` | All three | The financial pipeline |
| Any UI change in `fam/ui/payment_screen.py`, `fam/ui/admin_screen.py`, or `fam/ui/widgets/payment_row.py` | All three | These drive the engine |
| Schema migration | All three | Plus inspect `test_schema.py` results carefully |
| Help-content / docs-only changes | Pytest only | Run gates 2 + 3 anyway; they cost nothing |
| New release of any kind | All three | Mandatory |

If a gate **fails**, do not work around it. Investigate, fix, re-run.
Use `git bisect` if the failing commit isn't obvious.

---

## 5. Adding new tests / invariants

When a change adds new behavior worth pinning:

* **Per-row math / reconciliation** → add to
  `tests/test_match_formula.py`, `tests/test_reconciliation.py`, or
  `tests/test_production_stress.py`.
* **A new audit-log surface** → update
  `tests/test_audit_coverage_gaps.py` (move it from the "gap" class
  to the "logged" class).
* **A new export** → add a CSV-vs-DB reconciliation test in
  `tests/test_export_reconciliation.py`.
* **A new edge case found during operations** → add a Phase to
  `scripts/v1_9_9_stress_sim.py` so future audits exercise it.
* **A new release-gate invariant** → add it as a numbered entry in §3
  above so the procedure stays in sync with reality.

---

## 6. When you find a regression

1. **Stop.** Do not tag, build, or release.
2. Reproduce the failure with `pytest -x -v <failing_test>` or by
   running the simulation that flagged it.
3. Fix the underlying code (not the test).
4. Re-run **all three gates** — fixes in one area sometimes break
   another.
5. Add a new test that would have caught the regression.
6. Commit the fix and the new test together. Reference the gate that
   caught it in the commit message (e.g. `regression caught by
   v1_9_9_stress_sim Phase 5`).

---

## 7. Output expectations

A clean release-gate run looks like this (counts will grow over time;
exact numbers are not the contract — pass status is):

```
[1/3] Pytest suite ........................................ 2 090 passed
[2/3] Production simulation ............................... 43 PASS / 0 FAIL
[3/3] v1.9.9 stress simulation ............................ 34 PASS / 0 FAIL

  RELEASE AUDIT: PASS
  All three gates clean. Safe to tag and build.
```

If any gate fails the runner exits non-zero and prints which gate to
investigate first.

---

## 8. The reconciliation contract (re-stated)

For every confirmed/adjusted transaction `T`, every market day `D`,
every report surface `R`, every CSV export `E`:

```
T.receipt_total
  = Σ T.payment_line_items.method_amount
  = Σ T.payment_line_items.customer_charged
  + Σ T.payment_line_items.match_amount

Σ T.receipt_total over D
  = Vendor Reimbursement total (UI report)  for D
  = Vendor Reimbursement CSV grand total    for D
  = FAM Match "Total Allocated"             for D
  = Detailed Ledger non-voided receipt sum  for D
```

**These equalities must hold to ±0¢.** A penny of drift in any one
reconciliation is a financial-integrity regression and a release
blocker, not a rounding curiosity.

---

## 9. References

* `docs/FINANCIAL_FORMULA.md` — formula reference with `file:line`
  citations.
* `docs/PRODUCTION_READINESS_v1.9.9.md` — inaugural full audit report
  (April 2026); template for future audits.
* `tests/test_production_stress.py` — comprehensive stress tests.
* `tests/test_audit_coverage_gaps.py` — pinned audit coverage and
  documented gaps.
* `tests/test_export_reconciliation.py` — CSV ↔ DB reconciliation.
* `scripts/production_sim.py` — three-session market simulation.
* `scripts/v1_9_9_stress_sim.py` — focused stress simulation.
* `scripts/run_release_audit.bat` — one-command runner.

---

*If you are reading this because you are about to ship a release:
go run `scripts\run_release_audit.bat` now. Don't skim, don't skip,
don't "I'll do it after." Run it.*
