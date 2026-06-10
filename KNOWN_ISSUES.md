# Known Issues & Planned Enhancements

Curated handoff list for teams carrying this codebase forward.
Current as of v2.0.9 (June 2026).

## Open bugs

### KI-1 — Per-vendor eligibility ignored for non-denominated methods (priority fix)

Multi-vendor orders proportionally split non-denominated payment
amounts (SNAP, Cash, or any method without a denomination) across
ALL vendors in the order by receipt share — ignoring the per-vendor
eligibility flags configured in Settings → Vendors. Denominated
rows respect eligibility (they bind to a vendor); non-denominated
rows do not.

- Where: `fam/ui/payment_screen.py`, `_compute_per_vendor_state`,
  Phase 2 loop (~line 728). The save path mirrors the same logic.
- Repro: 2-vendor order ($15 + $25), enter $6 of a 0-denomination
  method that vendor 1 doesn't accept → engine allocates $4.50/$7.50
  by receipt share instead of routing everything to the eligible
  vendor.
- Fix shape: filter the per-vendor remaining map by the method's
  `eligible` flag before computing shares; apply to both display
  and save paths; handle zero-eligible and capacity-exceeded cases.

### KI-2 — Randomized fuzz: per-customer match cap exceedable (deferred, accepted)

In synthetic 30–100 action sequences (create/adjust/void churn),
the engine can exceed a customer's daily match cap by cents-to-a-
few-dollars. Never observed in real use; deliberately NOT fixed to
avoid touching stable money paths for a theoretical case.

- Accepted failure state is EXACTLY: fuzz simulator seeds 2 and 5
  (I5 violations) + pytest seeds `[23]`, `[1005]`, `[105]`.
  ANY other fuzz failure is a NEW bug.
- Consequence: gate 4 of `scripts\run_release_audit.bat` is
  expected-red (verify the seeds match before proceeding); CI
  deselects these by name (see `.github/workflows/tests.yml`
  comments).
- If ever revived, the agreed approach: a confirm-time cap clamp at
  the save boundary (same pattern as the existing Layer 2A/2B/2C
  guards) + a per-transaction adjustment limit (default 3) — no
  core engine math changes.

## Designed but unbuilt (full requirements exist)

### KI-3 — External Payments Entry (the next major feature)

Generalize the FMNP Entry screen so market managers can record ALL
paper scrip collected from vendors at end of market (Food RX, Food
Bucks, future methods), with per-method reimbursement math:

```
vendor_owed = face × match%  +  (face, unless vendor cashes the original)
```

| Method | Match | Vendor cashes? | FAM owes ($10 face) |
|---|---|---|---|
| FMNP | 100% | yes | $10 (the match) |
| Food RX | 100% | no | $20 (face + match) |
| Food Bucks | 0% | no | $10 (face only) |

Key requirements settled with the coordinator (June 2026): match %
and denomination inherit from existing payment-method settings (no
new money fields); two new per-method toggles (external-enabled,
vendor-cashes-original); entries snapshot their config (settings
changes never re-value history); every synced row carries a
plain-English "Reimbursement Basis" column; existing sheet tabs are
frozen — additions only, old app versions must not break.

### KI-4 — Order-level EBT settlement report

Coordinators match paper EBT receipts (one per customer order,
spanning vendors) against the system, but the Detailed Ledger is
per-vendor-transaction. Needed: one row per customer order with
order-level SNAP totals + timestamps. Related: the Generated
Rewards report tab ignores the market/date filters (deliberate v1
simplification — `fam/ui/reports_screen.py` ~line 1250).

### KI-5 — Seed data: JH Food Bucks should default to 0% match

Food Bucks are earned from already-matched SNAP purchases; matching
them again at spend double-dips. `fam/database/seed.py` (~line 101)
seeds 100%. Existing markets must verify their setting manually.

## Notes for a web/mobile evolution

The portable core is the business logic, not the UI: the integer-
cents engine (`fam/utils/calculations.py`), the schema + migration
history (`fam/database/schema.py`), the invariants
(`docs/SYSTEM_INVARIANTS.md`), and the financial contract
(`docs/FINANCIAL_FORMULA.md`). The 3,600+ test suite doubles as a
regression spec for any reimplementation. See SUCCESSOR_NOTES.md.
