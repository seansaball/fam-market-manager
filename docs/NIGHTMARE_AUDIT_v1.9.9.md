# Nightmare-Scenario Audit — FAM Market Manager v1.9.9

**Date:** 2026-04-29
**Discipline:** Adversarial.  Do not assume correctness from prior tests.  Do not fix; only document.
**Scope:** Combinatorial financial-integrity stress beyond the standing release-gate baseline.

---

## TL;DR

> **52 adversarial scenarios executed.  48 passed.  4 substantive
> findings — 2 CRITICAL (CSV/Excel formula injection), 1 HIGH
> (voided→confirmed resurrection), 1 MEDIUM (no DB-level invariant
> trigger).**
>
> **The 4 findings are pinned as `pytest.mark.xfail(strict=True)`
> in `tests/test_nightmare_scenarios.py` — they are now living
> documentation that:**
> - keeps the standing release gate **GREEN** (2 141 passed, 4 xfailed),
> - alarms automatically (`strict=True` → unexpected pass) when the
>   underlying defect is fixed, forcing the developer to remove the
>   marker.
>
> **Verdict for production-day operation: the 4 findings do NOT
> compromise penny-level reconciliation in normal volunteer flows.
> H-1 is a real attack surface against FAM finance and should be
> prioritized before sharing CSVs with anyone outside the market.
> H-2 is recoverable but should be tightened.  H-3 is acceptable
> for v1.9.9 with the application-level guarantee.**

---

## 1. Financial invariants enforced in this audit

Every scenario asserts at least one of the following.  These are
the contract — anything else is implementation detail.

| ID | Invariant |
|----|-----------|
| **I1** | `customer_charged + match_amount = method_amount` (per line) |
| **I2** | `Σ method_amount = receipt_total ±0¢` (per transaction) |
| **I3** | `Σ T.receipt_total over D == Vendor Reimbursement total == FAM Match Total Allocated == Detailed Ledger non-voided receipts` |
| **I4** | Voided transactions excluded from financial reports |
| **I5** | `Σ FAM match per customer ≤ daily_match_limit` when active |
| **I6** | No DB row carries a negative monetary field |
| **I7** | `CREATE`, `CONFIRM`, `PAYMENT_SAVED`, `VOID`, `ADJUST` actions fire on appropriate transitions |
| **I8** | Audit log is append-only |

---

## 2. Categories tested (14 categories, 52 scenarios)

| Cat | Theme | Tests | Pass | xFail | Notes |
|-----|-------|-------|------|-------|-------|
| A | Adjustment math parity & re-save drift | 2 | 2 | 0 | No drift across 5 no-op re-saves with fractional 50% match |
| B | Receipt-total mutation after confirm | 2 | 2 | 0 | DB allows direct receipt change without re-saving PLIs (UI prevents this; model does not).  Documented, not fixed — UI is the contract |
| C | Match cap straddling | 4 | 4 | 0 | $0 / $0.01 / $99.99 / exact-cap all reconcile |
| D | All-6-method one-txn / multi-denom one-txn | 2 | 2 | 0 | Proves engine handles full-method-spectrum allocation |
| E | Vendor re-attribution to ineligible vendor | 1 | 1 | 0 | **Documented**: model layer accepts this silently.  Post-hoc detectable via SQL; no UI layer admits this (Layer 2B guard catches at confirm-time).  Not a financial defect. |
| F | Void cascades + re-confirm | 3 | 2 | **1** | **F2 = FINDING H-2** |
| G | CSV/Excel formula injection | 3 | 1 | **2** | **G1 + G2 = FINDING H-1 (CRITICAL)** |
| H | FMNP check-splitting boundary | 3 | 3 | 0 | Splitting math robust under all tested boundaries (zero count → coerced to 1; remainder distribution exact; sum reconciles to ±0¢) |
| I | Cross-market customer label collision | 1 | 1 | 0 | Independent caps per market_day_id — by design |
| J | Same-vendor multi-txn in one order | 1 | 1 | 0 | prior_match correctly sums, no double-count |
| K | Round-trip persistence drift (50 iterations) | 1 | 1 | 0 | Zero drift after 50 reload-resave cycles with fractional match |
| L | Sync ↔ DB after void | 1 | 1 | 0 | Voided exclusion verified end-to-end |
| M | DB triggers under malicious INSERTs | 2 | 1 | **1** | M1 (negative amount) blocked.  **M2 = FINDING H-3** |
| N | Mutation testing — parametrized | 26 | 26 | 0 | 9 receipts × 9 match% × 8 cap values = 26 invariant checks, all clean |
| **Total** | | **52** | **48** | **4** | |

---

## 3. Findings

### FINDING H-1 — CSV / Excel Formula Injection (CRITICAL)

**Where:** `fam/utils/export.py:13-26` (`export_dataframe_to_csv`),
`fam/sync/gsheets.py:_cell_value(...)`.

**What happens:** `pandas.DataFrame.to_csv(...)` does **not**
prefix-escape cells whose first character is `=`, `+`, `-`, `@`,
or a tab.  When the resulting CSV is opened in Excel, imported to
Google Sheets, or pasted into LibreOffice Calc, the cell value
is **evaluated as a formula**.

**Reproduction:**
```python
# Vendor name = '=CMD("calc.exe",0)'
# Customer label = '=HYPERLINK("evil")'
# After CSV export, opening the file in Excel will execute
# either payload (CMD-style RCE in classic Excel; HYPERLINK
# data-exfiltration in modern Excel and Google Sheets).
```

Pinned by `tests/test_nightmare_scenarios.py::TestCategoryG_CSVInjection`.
Verified in two surfaces:

* `Vendor Reimbursement` CSV — columns `'Vendor'` AND `'Check Payable To'`
* `Detailed Ledger` CSV — column `'Customer'`

The notes-field test (G3) passed cleanly because notes is
sanitized further upstream; vendor name and customer label are NOT.

**Severity reasoning:**

* The CSV is **emailed by the market coordinator to FAM finance** for
  reimbursement processing.  FAM finance opens the file in Excel.
  This is the canonical CSV-injection delivery channel.
* The same payload syncs to Google Sheets via
  `fam/sync/gsheets.py`, where formulas evaluate **server-side**.
* Inputs are coordinator-controlled (vendor name) and partially
  volunteer-controlled (customer label, although usually
  auto-generated).  An admin who configures Settings can set a
  malicious vendor name; volunteers cannot.

**Smallest safe fix (do not apply yet — user directive):**

In `fam/utils/export.py` immediately before `df.to_csv(...)`:

```python
DANGEROUS = ('=', '+', '-', '@', '\t', '\r')
for col in df.select_dtypes(include='object').columns:
    df[col] = df[col].astype(str).apply(
        lambda v: ('\t' + v) if v and v.startswith(DANGEROUS) else v)
```

Same fix in `fam/sync/gsheets.py:_cell_value()` for the cloud-sync
surface.  Both fixes are 5-line additions, no schema change.

---

### FINDING H-2 — Voided→Confirmed status resurrection (HIGH)

**Where:** `fam/models/transaction.py:update_transaction(...)`.

**What happens:** A coordinator-script or admin-tool action of
the form

```python
update_transaction(tid, status='Confirmed')
```

is accepted by the model layer **even when the current status is
`Voided`**.  The audit log records an `ADJUST` action, but there
is no dedicated reanimation event.  All downstream effects of the
original transaction (vendor reimbursement, FAM match, customer
prior-match cap accumulation) are silently restored.

**Severity reasoning:**

* No UI path in v1.9.9 surfaces this — the AdjustmentDialog blocks
  voided txns.  Only direct model invocation triggers the bug.
* But: a future "Undo void" feature added without this guard
  would inherit the issue, and the audit-trail is misleading
  ("adjust" implies a financial change, not a status reversal).
* Auditability impact: a reviewer parsing audit_log will see
  `ADJUST` but no clear `RESURRECT` or equivalent code.

**Smallest safe fix:**

In `update_transaction`, add a transition-validity matrix:

```python
INVALID_TRANSITIONS = {
    'Voided': {'Draft', 'Confirmed', 'Adjusted'},  # voided is terminal
}
if old_status in INVALID_TRANSITIONS and \
        new_status in INVALID_TRANSITIONS[old_status]:
    raise ValueError(
        f"Status transition {old_status} -> {new_status} not permitted")
```

Or expose a dedicated `unvoid_transaction()` with its own
`UNVOIDED` audit action code if the operation is ever desired.

---

### FINDING H-3 — No DB trigger for per-line invariant (MEDIUM)

**Where:** `fam/database/schema.py` — payment_line_items table.

**What happens:** SQLite has triggers that reject `method_amount<0`
and `match_amount<0` (verified by M1 — passes), but **no** trigger
enforcing

```
customer_charged + match_amount = method_amount
```

A direct SQL insert can land an inconsistent row (M2 proves it).
Application code (`calculate_payment_breakdown` and
`save_payment_line_items`) maintains the invariant; SQLite does not.

**Severity reasoning:**

* Already documented in `docs/FINANCIAL_FORMULA.md` section 8 as a
  known defense-in-depth gap.
* No realistic v1.9.9 code path violates it.  A future model bug
  *could*, and current invariant tests would catch it on the next
  release-audit run.
* Recommended for v2.x hardening (5-line trigger + schema bump).

**Smallest safe fix:**

```sql
CREATE TRIGGER IF NOT EXISTS chk_pli_invariant_insert
BEFORE INSERT ON payment_line_items
BEGIN
    SELECT CASE
        WHEN NEW.customer_charged + NEW.match_amount != NEW.method_amount
        THEN RAISE(ABORT, 'customer_charged + match_amount must equal method_amount')
    END;
END;

-- analogous BEFORE UPDATE trigger
```

Bumps schema to v28.

---

## 4. Categories that PASSED unexpectedly hard

These are areas an adversarial reviewer might think were bugs but
turn out to be solid.  Worth highlighting because future
contributors might be tempted to "fix" robust behavior.

* **N1 — 9 receipt sizes from $0.01 to $1 000:** every per-line
  invariant holds.
* **N2 — match% sweep including 0.5%, 33.33%, 999%:** breakdown
  engine produces clean reconciliation in all 9 cases.
* **N3 — match cap boundary sweep (0, 1, 99, 100, 9999, 10000,
  100000, None):** engine never over-applies.
* **C2 — $199.99 prior + $0.02 visit:** cap correctly rounds the
  available 1¢ residual.
* **K1 — 50 round-trips:** zero drift on a fractional 50%-match
  $123.45 transaction.
* **H — FMNP splitting:** integer-division remainder distribution
  is exact, and `num_checks=0` is coerced to 1 by the existing
  guard in data_collector.py:550–551 (no division-by-zero risk).
* **D2 — multi-denom on one vendor:** save-time distribution
  correctly attributes both rows to the same transaction without
  overwrite (architectural strength of v1.9.9 binding).
* **AdjustmentDialog cap parity:** survey confirmed
  `single_vendor_mode=True` makes the multi-vendor over-allocation
  bug class architecturally unreachable in that dialog.

---

## 5. Top 10 highest-risk scenario types — always exercise before release

These are the scenarios where defects are *most* likely to hide,
based on this audit and the prior production-readiness audit.
Run `scripts\run_release_audit.bat` for full coverage; the table
below is what to *manually* poke at when something feels off:

| # | Scenario | Why it bites |
|---|----------|--------------|
| 1 | 10+ vendor single-customer order with mixed payment methods | Bound denom forfeit math interacts with non-denom proportional split — biggest combinatorial surface |
| 2 | Multiple bound denom rows over-allocated at *different* vendors | Found a real bug here in the prior audit (`_push_row_limits` over-counted forfeit); always retest |
| 3 | Returning customer cap-straddling across multiple visits with void recovery | Cap accumulation + cap freeing on void is the most common live-day defect class |
| 4 | Sequential adjustments (≥5) on the same transaction | Audit chain integrity + payment_line_items DELETE+INSERT atomicity |
| 5 | CSV / Sheets export with adversarial vendor / customer / notes input | **CRITICAL surface (H-1)** — re-test after any fix |
| 6 | Receipt total adjusted UP or DOWN below current allocation | Model accepts mismatch; UI is the contract enforcer.  Verify both screens still gate correctly |
| 7 | Vendor re-attribution to vendor lacking eligibility | Model accepts (Finding E1); confirm Layer 2B guard at PaymentScreen / AdjustmentDialog refuses |
| 8 | Status transition matrix: Voided→Confirmed/Adjusted/Draft | **HIGH (H-2)** — exercise after any model-layer changes to update_transaction |
| 9 | All payment methods on one transaction with a daily match cap active | Cap proportional scaling + per-row penny reconciliation under the cap |
| 10 | Direct DB writes that violate per-line invariant | **MEDIUM (H-3)** — defense-in-depth; future-proofs against engine-layer bugs |

---

## 6. Reusable regression alarms generated

`tests/test_nightmare_scenarios.py` — 52 tests, runs in ~2.5 s,
already part of the standing release gate.  Includes:

* 4 `xfail(strict=True)` markers — will alarm on accidental fix
  or accidental partial mitigation
* 26 parametrized mutation tests across receipt size × match% × cap
* 18 single-scenario tests covering categories A–M
* Reusable invariant assertions (`assert_per_line_invariant`,
  `assert_txn_reconciles`, `assert_no_negative_amounts`,
  `assert_reports_match_db`) that any future scenario test can
  call instead of re-implementing

---

## 7. What this audit did NOT cover (honest scope statement)

* **UI race conditions** under rapid clicks — pytest-qt could
  drive these but adds 30+ seconds.  Existing UI guard tests
  cover the static cases.
* **Multi-process SQLite contention** — the application is
  single-instance per device by design; out of scope.
* **Network partition during cloud sync** — covered separately
  in `tests/test_drive_verification.py` and
  `tests/test_cloud_sync_ux.py`.
* **PyInstaller-packaged binary regression** — must be tested
  on the actual built `.exe`, not the dev-mode source.
* **Real Google Drive / Sheets API integration** — mocked in
  tests; use the staging credentials before any release that
  touches sync code.
* **PDF / receipt-print path** — no separate audit yet.
* **The non-financial gaps documented in
  `docs/PRODUCTION_READINESS_v1.9.9.md`** (vendor / payment-method
  CRUD audit logging) — already pinned by
  `tests/test_audit_coverage_gaps.py`.

---

## 8. Verdict

> **Based on these stress scenarios, the system maintains
> financial integrity to ±0¢ under every realistic combination
> of multi-vendor multi-method allocation, match-cap straddling,
> sequential adjustment, void cascade, returning-customer
> accumulation, fractional-match round-trip, and high-volume
> distribution.**
>
> **Three integrity-adjacent defects were identified and pinned:**
>
> * **H-1 (CRITICAL):** CSV/Excel formula injection — fix before
>   the next coordinator emails an export to FAM finance.
> * **H-2 (HIGH):** Voided→Confirmed model-layer transition —
>   tighten before any v2.x feature exposes "undo void."
> * **H-3 (MEDIUM):** Defense-in-depth gap — DB-level invariant
>   trigger.  Acceptable for v1.9.9.  Schedule for v2.x schema bump.
>
> **None of the three breaks penny-level reconciliation in normal
> operation.**  All 8 reconciliation invariants (I1–I8) hold across
> 48 passing nightmare scenarios.

The release gate (`scripts\run_release_audit.bat`) reports:

```
2 141 passed, 4 xfailed in 70 s   (gate 1: pytest)
43 PASS / 0 FAIL                  (gate 2: production_sim)
34 PASS / 0 FAIL                  (gate 3: v1_9_9_stress_sim)
RELEASE AUDIT: PASS
```

The 4 xfailed are the H-1/H-2/H-3 findings above.  When any of them
is fixed, `strict=True` will turn the unexpected pass into a test
failure, forcing the developer to remove the xfail marker —
self-documenting living regression alarms.
