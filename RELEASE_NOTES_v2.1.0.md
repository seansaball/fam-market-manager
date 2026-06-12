# FAM Market Manager v2.1.0 — "External Payments Entry"

**Previous public release:** v2.0.9 (May 2026)
**Release date:** TBD — release held open while further enhancements land (June 2026)
**Schema version:** 41 (upgraded from 37 via four additive migrations — automatic pre-migration backup)

---

v2.1.0 generalizes the FMNP Entry screen into **External Payments Entry** (ENH-002). Coordinators can now record ANY external-enabled payment method's scrip collected from vendors at end of market day — FMNP, Food RX, JH Food Bucks, and future methods — not just FMNP paper checks. **FMNP remains the default selection and behaves exactly as before.**

Why this matters: until now, Food RX tokens and Food Bucks collected from vendors at close-out had no first-class home in the app — coordinators tracked them on paper or shoehorned them into transactions. Now every external collection flows through the same audited, synced, three-way-reconciled pipeline FMNP checks always have.

Three more headline changes ship alongside it:

- **SNAP Settlement report** (ENH-003) — one row per EBT terminal swipe, for matching the terminal's paper receipts against the system. If customer 1 ran $21.23, you'll find one $21.23 line.
- **Verified checkboxes for market managers** (ENH-006) — working-view tick-offs on the SNAP Settlement and Vendor Reimbursement reports for end-of-market reconciliation. Purely a manager's working aid: ticks are local to the laptop, never sync, and never touch a single reimbursement number.
- **Money boxes now type the standard way** (ENH-007) — the ATM-style entry that "threw a random extra 0" is gone; every money field types calculator-style, exactly what you see.

Plus a Market Day report filter, a whole-month quick pick, the Generated Rewards tab now respecting filters, and a chart/walkthrough polish pass — details below.

---

## The money table — what FAM owes the vendor

Different programs reimburse differently. The screen knows the difference so the coordinator doesn't have to. Worked examples at $10 face value:

| Method | Vendor doubles? | Vendor cashes original? | FAM owes vendor |
|---|---|---|---|
| **FMNP** | Yes (100%) | YES — cashes the check with the program | **$10 — the MATCH only** |
| **Food RX** | Yes (100%) | No — FAM collects, mails to Food Trust | **$20 — face + match** |
| **Food Bucks** | No (0%) | No — FAM collects | **$10 — face only** |

The formula behind every row:

```
FAM owes = round(face × match% ÷ 100) + (face, unless the vendor cashes the original)
```

Match % and denomination come from the method's **existing** settings — no new money fields anywhere.

---

## What's new

### The FMNP Entry screen is now External Payments Entry

A method selector at the top of the screen lists every external-enabled payment method. FMNP is pre-selected by default; coordinators who only ever record FMNP checks will notice nothing different. Selecting Food RX or Food Bucks switches the screen's denomination, match math, and payout preview to that method's settings.

### Setup is two checkboxes per method

**Settings → Payment Methods → Edit → External section:**

1. **"Accept external matching"** — makes the method available on the External Payments Entry screen.
2. **"Vendor cashes the original instrument"** — ON means the vendor keeps and cashes the physical scrip with the issuing program (the FMNP model), so FAM owes the match only. OFF means FAM collects the scrip, so FAM owes face + match.

Plus verifying the method's existing denomination and match %. That's the entire setup.

Photo requirements inherit each method's **existing** `photo_required` setting — no new photo configuration. Food RX and Food Bucks default to off; turn the setting on for a method if its entries should capture photos.

**One-time setup for Food RX / Food Bucks external collection:**

- Edit the method, tick **"Accept external matching"**, leave **"Vendor cashes original" OFF** (FAM collects these).
- Verify denomination: **$10** Food RX / **$2** Food Bucks.
- Verify match %: **100%** Food RX / **0%** Food Bucks.
- **FMNP needs nothing — it's pre-enabled** and behaves exactly as before.

### Every entry snapshots its method config at save time

Each saved entry captures the method's denomination, match %, and vendor-cashes-original flag **at the moment of save**. Later settings changes never re-value history. Corrections are **void + re-enter** (consistent with denominated-transaction discipline). Every synced row carries a plain-English **"Reimbursement Basis"** — e.g. `Face + match ($10.00 × 2.0)` — so finance can audit any row in isolation, months later, without knowing what the settings were that day.

### Guards at every step

- **Live payout preview** as the coordinator types — the screen shows what FAM will owe before anything is saved.
- **Save-time confirmation**: "FAM will owe \<vendor\> $X.XX" — the coordinator confirms the dollar consequence, not just the keystrokes.
- **Denomination whole-multiple check** — a $2 Food Bucks method can't record a $7 collection.
- **Large-amount warning** for entries that look like typos.
- **Booth + external double-count review prompt** — if the same vendor has both booth transactions and external entries for the same method on the same day, the app prompts for review so the same scrip isn't paid out twice.
- **Config linter at the settings gate** — an external-enabled method must have a denomination (hard block: paper scrip always has a face value, and the denomination drives the whole-multiple check and instrument count). Vendor-cashes-original combined with 0% match — which would make FAM owe $0.00 on every entry — draws a confirm-anyway warning.
- **Closed-market-day entry works for ALL external methods** — the after-the-fact entry path FMNP checks have always had now covers Food RX, Food Bucks, and every future external method, so late token collection is never blocked by a closed day.

---

## Reports & Google Sheets changes

- **NEW required sheet tab: "External Payment Entries"** — the per-entry audit layer for **all** external scrip, **FMNP included**. One row per entry, each carrying its snapshot config, the derived FAM Owes Vendor amount, and a plain-English Reimbursement Basis. All photo links ride in one cell per entry.
- **The "FMNP Entries" tab is deprecated.** When a market upgrades, its complete FMNP history appears on External Payment Entries automatically on the first sync, and its rows are **removed from the old tab** on the next full sync. Markets still on older versions keep writing the old tab until they upgrade. Once every market is upgraded, the tab will be empty and the coordinator deletes it. **During the transition, read FMNP from both tabs** — each market's records live on exactly one of them, never both, so there is no double-counting.
  - Retired with the old tab: per-check row splitting (the new tab is one row per entry) and booth-paid FMNP "PAY-" rows (booth FMNP — rare — remains fully visible in the Detailed Ledger as part of its transaction).
- **Vendor Reimbursement** gains **"\<Method\> (External)"** columns that add into Total Due to Vendor. The **FMNP (External)** column keeps its exact meaning and its exact feed — vendor checks do not change.
- **FAM Match Report** gains **"\<Method\> (External)"** rows.
- **Detailed Ledger** gains **EXT-** rows.
- **Reports screen** gains a new **"External Payments"** summary card.
- **Ledger backup** gains an external section.

> **Note for existing spreadsheets:** new columns appear at the **end** of the header row. Coordinators may drag them next to FMNP (External) if they prefer — this is safe; writes go by header name, not position.

---

## New report: SNAP Settlement — match the EBT terminal's receipts (ENH-003)

The EBT terminal runs **one charge per customer order**, but an order can span several vendors — so the Detailed Ledger scatters a single swipe across several rows, and matching the terminal's paper receipts against the system meant re-adding rows by hand.

The new **SNAP Settlement** tab (Reports — second tab, right after Vendor Reimbursement) shows **one row per customer order**: timestamp, customer label, zip code, market, how many vendor receipts the order spanned, and the **order-level SNAP total** — the same figure the volunteer acknowledged on the payment confirmation dialog when the card was run. If customer 1 ran $21.23 of SNAP, the report shows one $21.23 line. Rows run oldest-first, like the terminal's paper stack.

- **A Verified checkbox on every row** turns the tab into a working page: tick rows off as you match each paper receipt (verified rows turn green). Ticks save immediately on that laptop and survive filter changes and app restarts, so the exercise can be paused and resumed. Verification is a working note, not a financial record — it never syncs and never changes a report total. (Schema v38 → v39: one additive column, `customer_orders.settlement_verified_at`.)
- **Every order with SNAP appears** — including orders that earned no rewards.
- **Date + Market filters apply.** The Vendor and Payment Type filters are deliberately ignored on this tab — a vendor sub-total would never match the paper receipt.
- **Local-only**: on-screen + CSV export (the CSV includes the Verified column). No Google Sheets tab — every dollar in it already syncs via the Detailed Ledger rows it derives from.
- Voided transactions drop out automatically.

### New "Market Day" filter on the Reports screen (ENH-005)

Coordinators reconcile per market day — "show me last Tuesday" — but the only date control was a from/to range, which meant pinning both ends to the same date and was ambiguous when two markets ran on the same day. The filter bar now has a **Market Day** dropdown listing every market day as "date — market" (most recent first). Pick one and **every report tab narrows to exactly that day**. Market Day and the date range are last-touched-wins alternatives: picking a day resets the range to "All Dates", and applying a range switches Market Day back to "All Market Days" — the two can never appear set at the same time. This is the intended entry point for the post-market-closure settlement exercise.

### Vendor Reimbursement gains an end-of-market Verified column too (ENH-006)

The same working-page idea, for the manager's end-of-market vendor walk: confirming each vendor's receipt total in person. The on-screen Vendor Reimbursement report shows a **Verified** checkbox per vendor row (first column) — tick vendors off as you talk to them; verified rows turn green, and ticks persist on that laptop, surviving filter changes and restarts. **Every time scope keeps its own independent mark:** a day tick (the vendor walk, via the Market Day picker), a whole-month tick (the check-cutting pass, via the month quick pick), and a custom-range tick are separate facts — verifying days never makes the month read verified, and unchecking a month never unchecks the days inside it. Only ticked combinations are stored, so there is no state explosion. With no time scope at all (All Dates + All Market Days) the column shows an inert dash — there is no scope to attach a mark to. **Verification marks are a reconciliation aid for the market manager only — ticked or not, they have zero effect on FAM reimbursement**, never sync to the shared sheet, and are invisible to every money calculation. The table holds its market/vendor walking order (header sorting is off so checkboxes always stay with their rows); the CSV export gains a Verified column when a scope is active. (Schema v39 → v41: two small local tables, `vendor_day_verifications` + `vendor_range_verifications`.)

### Whole-month quick pick on the date filter

The date popup now offers **"Whole month"** as an either/or alternative to the custom from/to range — pick "June 2026" and the reports cover exactly that month. Built for the month-end reconciliation pass.

## Money boxes now type the standard way (ENH-007)

Field-reported by volunteers: typing into the Receipt Total box "threw a random extra 0 at the end." The cause was the app's ATM-style typing system (digits entered at the penny position and pushed left — typing "85" produced $8.50, and the decimal key was silently ignored). Volunteers type calculator-style, so amounts came out wrong in confusing, random-looking ways.

**Every money box in the app now types exactly what you see:** type `85` and it means $85; type `85.50` (or `85.5`) and it means $85.50; the decimal key works; the value formats to two decimals when you leave the field. Highlighting a value and hitting Backspace deletes it, and typing over a highlight replaces it — including when the highlight grabs the `$` sign. Tabbing into a field still selects the old value so typing replaces it, and scroll-wheel still can't change values by accident. No backend math changed — this only fixes what amounts get *entered*, which protects every total downstream.

### Bundled fix: Generated Rewards tab now respects filters

The Generated Rewards tab previously ignored the report filters entirely (a deliberate v1 simplification), which made it confusing for per-market reconciliation — and made it a tempting but wrong stand-in for EBT matching, since it only lists orders that fired a reward. It now honors the **Date + Market** filters like the rest of the reports; Vendor / Payment Type filters don't apply to it (rewards belong to a customer order, not a vendor). The right tool for EBT matching is the new SNAP Settlement tab.

---

## UI polish & fixes

- **Charts face-lift.** Every payment method now keeps **one stable color across all charts** — SNAP is the same color on the pie, the trend line, and the vendor breakdown, every time, regardless of which methods appear in the filtered data. The payment-methods pie is now a **donut with the total in the center**, the trend chart's axis is **dollar-formatted**, and all charts render in the **app's own font** instead of matplotlib's default.
- **Help walkthrough layout fixes.** The animated walkthrough's stages now **center correctly at any window size**; Stage 3's dual-path payment-flow row **no longer clips** on the right; and Stage 5's laptop-to-cloud **sync arrow is now visible** — it was previously painted over by the Google Sheets tile, so the "data flows up to the cloud" beat never showed.
- **Documentation correction.** Old all-time-cumulative **Vendor Reimbursement** sheet rows are **replaced automatically** on each device's first v2.0.9+ full sync — the v2.0.9 notes wrongly described a manual orphan-cleanup step that is not needed.

---

## Compatibility — explicit statement

- **All money columns are unchanged.** No renames, no semantic changes to Vendor Reimbursement, FAM Match, or Detailed Ledger; the **FMNP (External)** column keeps its exact meaning and its exact feed. Vendor reimbursement totals are identical before and after upgrading.
- **One tab transition, by design:** the per-entry **FMNP Entries** audit tab is deprecated in favor of **External Payment Entries** (see above). This is the only existing tab whose content changes, and only for upgraded markets — each market's rows move atomically with its own upgrade.
- **Old app versions keep writing the same sheets and are unaffected.** Mixed fleets need **no coordinated upgrade** — an old device simply doesn't have the new feature and keeps using the old tab. (One bound: an old device cannot open a database already upgraded by v2.1.0; each device's own data migrates when that device upgrades, with an automatic pre-migration backup.)
- **Schema v37 → v41, additive only** (four migrations: v38 external-payments columns, v39 settlement-verified column, v40/v41 vendor-verification tables). They run automatically on first launch, preceded by an automatic backup; any failure rolls back cleanly.

---

## Also in this release — ENH-001: Food Bucks seed match corrected

**Fresh-install seed data now sets JH Food Bucks to 0% match.** Reward-type scrip must not be matched again — the match was applied when the scrip was earned ($5 SNAP → $2 Food Bucks).

**Existing markets are NOT auto-changed.** If your market follows the standard rewards model, verify **Settings → Payment Methods → JH Food Bucks → match percent** and set it to **0%**.

---

## For the technical record

- **Snapshot discipline**: the payout is **derived, never stored** — every surface (entry-screen preview, save-time confirmation, sheet collectors, reports screen, CSV export, ledger backup, coherence auditors) computes it from the entry's snapshot config via the single payout module. There is no second copy to drift.
- **Payout module**: `fam/utils/external_payout.py` — the single home for the formula and the Reimbursement Basis wording. Pure integer-cents arithmetic; one rounding step, round-half-away-from-zero (Python's `round()` is banker's rounding and floats drift — neither is acceptable for money). Golden-pinned identity: at 100% match with vendor-cashes-original ON, the payout equals face value byte-for-byte — today's FMNP behavior, preserved.
- **EP invariants**: documented as **Layer 10 (EP-series)** in `docs/SYSTEM_INVARIANTS.md`, checked by `tests/_coherence.py::audit_external_entries` and pinned by `tests/test_external_invariants.py`.

---

## Upgrade

Standard installer or in-app updater (**Settings → Updates → Check for Updates**). The v37 → v41 migrations run automatically on first launch with a pre-migration backup. No coordinated fleet upgrade required — upgrade each laptop when convenient.

After upgrading, coordinators who want Food RX / Food Bucks external collection should perform the one-time two-checkbox setup above; FMNP users need to do nothing.
