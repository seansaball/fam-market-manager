# FAM Market Manager — Project Instructions & Specification

> **Purpose:** This file is the single source of truth for the FAM Market Manager
> application. It is written for an AI coding assistant or a new developer who
> needs to understand, maintain, or extend the project **without** access to
> previous conversation history. Keep this file up to date with every commit.
>
> **Last updated:** 2026-05-07 — v2.0.8 release in flight (first v2.x release reaching the field; consolidates v2.0.0 / v2.0.1 / v2.0.6 / v2.0.7 / v2.0.8 development cycles since v1.9.8/v1.9.9)

---

## 0. Where We Left Off (handoff note)

If you are a fresh AI session opening this repo, read this first:

- `fam/__init__.py` reports `__version__ = "2.0.8"`.  v2.0.8
  is the first v2.x release reaching the field — no public
  download has happened on any v2.0.x version yet.  It rolls
  up the v2.0.0 / v2.0.1 / v2.0.6 / v2.0.7 / v2.0.8
  development cycles since v1.9.8 / v1.9.9 into a single
  shipping version.  `RELEASE_NOTES_v2.0.8.md` and
  `FAM_Manager_v2.0.8_Release_Notes.html` are the sole
  release-notes-of-record and cover everything since v1.9.9
  framed as one consolidated release.  The intermediate
  v2.0.6 / v2.0.7 release-note files have been removed from
  the repo to avoid drift and confusion — v2.0.8 is the
  comprehensive log.
- Schema bumped v34 → **v37** through three forward migrations:
  - v34 → v35: `_migrate_v34_to_v35` backfills universal SNAP/Cash
    `vendor_payment_methods` rows for every vendor.
  - v35 → v36: `_migrate_v35_to_v36` adds
    `customer_forfeit_cents INTEGER NOT NULL DEFAULT 0` to
    `payment_line_items` (Phase B token-value forfeit).
  - v36 → v37: `_migrate_v36_to_v37` adds
    `user_capped INTEGER NOT NULL DEFAULT 0` to
    `payment_line_items` (per-row Auto-Distribute toggle persistence).
- Headline policy changes (v2.0.7 hotfix): **SNAP and Cash are
  universally bound to every vendor and cannot be unassigned**;
  denomination preservation through adjustments (engine snap-back
  + save-layer guard); Adjustment safety-gate for denominated
  transactions; single-vendor multi-receipt allocation; vendor
  reimbursement after voids; photo dedup cache cleanup on void /
  FMNP delete / FMNP edit; cap-bound impossible-to-balance
  recommendation (Layer 2A.1 enriches the mismatch dialog).
- Headline policy changes (v2.0.8 follow-ups): **Customer Forfeit
  (Phase B)** is a first-class concept &mdash; surfaced as a
  summary card on Payment Screen + dedicated column in reports;
  **denomination-integrity in reports** &mdash; per-method columns
  show `customer_charged + customer_forfeit_cents` (denomination-
  true), reconciliation `Σ(method-cols) + FAM Match - Customer
  Forfeit + FMNP_External = Total Due`; **per-row ⚡ Auto-Distribute
  toggle** with green/grey states + radio invariant (only one
  Active overflow target at a time); `user_capped` flag persisted
  to DB so Locked rows survive draft save/restore + adjustment
  round-trips; **FMNP "All Market Days" filter** with inline hint
  on the disabled Save button; Auto-Distribute cap-deficit Pass 2
  fallback to unmatched non-denom auto rows; AdjustmentDialog
  parity (`user_capped` propagated to the impact preview engine
  call); status-filter centralization
  (`fam.models.transaction.active_tx_status_clause()`); spinbox
  empty-string crash fix; sync watchdog.

  An earlier v2.0.7 attempt added a `_auto_rebalance_non_denom`
  method that fought the engine's deterministic cap-aware Path B +
  Pass 4 — it was REVERTED.  See the source pin in
  `tests/test_cap_bound_split_recommendation.py::TestRevertedAutoRebalance`
  for the why; do NOT re-introduce it.  The split-orders
  recommendation (surfaced in the enriched Layer 2A dialog and a
  new `ts-payment-screen-hard-block` troubleshooting flow) is the
  durable user-facing resolution for the cap-bound case.

- The v2.0.6 production season release was tagged and published
  2026-05-06 (commit `4050f51`,
  https://github.com/seansaball/fam-market-manager/releases/tag/v2.0.6).
  Auto-update path verified end-to-end on a real v1.9.8 device.
  Headline additions in v2.0.6: per-vendor payment-method
  eligibility, configurable rewards engine, redesigned Payment
  Confirmation Dialog, multi-workstation cloud-sync invariant
  systematically guarded, market-rename code-shift protection, photo
  dedup cache cleaned on void/delete/replace.  See
  `RELEASE_NOTES_v2.0.6.md` for the full release log.
- Schema is at **v35** (after the v2.0.7 hotfix migration runs).
  Migration chain since the last release:
  - v23 → v24: `vendor_payment_methods` junction (per-vendor eligibility)
  - v24 → v25: `payment_methods.is_system` column + Unallocated Funds seed
  - v25 → v27: defensive cleanup of abandoned v26 (no v26 migration)
  - v27 → v28: `chk_pli_invariant_*` triggers enforce
    `customer_charged + match_amount = method_amount` per row
    (system-method `Unallocated Funds` exempt)
  - v28 → v30: indexes for scaling (skipped v29)
  - v30 → v31: `chk_payment_amount_update` (UPDATE non-negativity) +
    `chk_transactions_voided_one_way` (terminal Voided) triggers
  - v31 → v32: composite scaling indexes for multi-year deployments
  - v32 → v33: `chk_pli_uf_zero_*` triggers enforce
    `customer_charged=0` AND `match_amount=0` on Unallocated Funds
    rows (defense-in-depth that fresh installs now correctly receive)
  - v33 → v34: dedupe pre-existing `schema_version` rows + add
    `CREATE UNIQUE INDEX idx_schema_version_unique ON schema_version
    (version)` so future Reset cycles can't accumulate duplicate rows
  - v34 → v35 (v2.0.7): `_migrate_v34_to_v35` backfills SNAP and
    Cash `vendor_payment_methods` rows for every vendor on first
    launch.  Idempotent.  Pairs with the new
    `is_universal_vendor_method(name)` helper in
    `fam/models/payment_method.py` and a defensive guard in
    `unassign_payment_method_from_vendor` that refuses to remove
    the universal bindings (silent return + WARN log).
- **Phase 6 engine consolidation (v1.9.10):** the canonical
  `fam.utils.calculations.resolve_payment_state(...)` is now the
  single source of truth for cap-aware + denomination-forfeit +
  Pass-4-give-back resolution.  Both `PaymentScreen._resolve_engine_state`
  and `AdjustmentDialog.get_new_line_items` delegate to it.  This
  eliminates the 10-place duplicated-cap-math drift class that
  caused 18 onsite-reported bugs.  AdjustmentDialog and PaymentScreen
  are intentionally NOT unified at the UI layer (deferred to v1.10);
  parity is enforced via `tests/test_adjustment_payment_parity_matrix.py`.
- New v1.9.10 test files (411 new tests over v1.9.9):
  - `test_cross_layer_parity_matrix.py` (240 cells: 24 scenarios × 10
    layer/invariant assertions covering cards, row labels, vendor
    breakdown, popup dialog, engine, DB)
  - `test_adjustment_payment_parity_matrix.py` (48 cells: every
    single-vendor scenario through both screens, must agree to ±0¢)
  - `test_engine_save_path_equivalence.py` (DB aggregates per
    method == engine post-forfeit per method)
  - `test_resolve_payment_state_equivalence.py` (canonical engine
    ≡ existing engine + forfeit chain — proves the consolidation
    safe)
  - `test_multi_adjust_chain.py` (5+ adjustments on same txn: all
    invariants hold)
  - `test_returning_customer_cap_chain.py` (4-5 orders one
    customer, void + adjust + cap accounting)
  - `test_app_restart_persistence.py` (fresh widget reload =
    byte-identical UI state)
  - `test_nightmare_scenarios_ui.py` (8 onsite-finding scenarios
    re-run through actual UI screens)
  - `test_ui_driven_fuzz.py` (25 seeds × 25-80 random UI actions,
    R1/V1/V5/X1/X2 invariants after every action; 1 known finding
    `xfail`'d for v1.10 PaymentRow refactor)
- All version refs to `v1.9.8`, `v1.9.7`, `v1.9.6`, `v1.9.5` etc. inside
  code comments, test docstrings, and in-app Help articles are intentional
  **historical markers** — do not "update" them.
- Before tagging *any* release (hotfix included), run the **mandatory
  Release Audit Gate** — `scripts\run_release_audit.bat` — which runs
  four gates: full pytest (**2 671+ tests**), production simulation
  (43 invariants), v1.9.9 stress simulation (34 invariants), and
  fuzz smoke (5 seeds × 100 actions).  All four must exit clean.
  Procedure documented in `docs/RELEASE_AUDIT_PROCEDURE.md`.  See §18
  below.  Then build a local distro via `build.bat` and have the user
  code-sign.

---

## 1. Project Overview

**FAM Market Manager** is a desktop POS/back-office application used at farmers
markets to track customer transactions, calculate Food Assistance Match (FAM)
subsidies, process payments, record FMNP checks, and generate reports for
vendor reimbursement. Deployed across multiple market locations, each on a
dedicated Windows PC.

| Stack         | Technology                          |
|---------------|-------------------------------------|
| Language      | Python 3.12+                        |
| GUI framework | PySide6 (Qt 6)                      |
| Database      | SQLite (WAL mode, foreign keys on)  |
| Charts        | matplotlib                          |
| Geolocation   | folium + pgeocode                   |
| Data export   | pandas                              |
| Packaging     | PyInstaller (Windows .exe)          |
| Cloud Sync    | gspread + google-auth               |
| Auto-Update   | urllib.request (stdlib)              |
| Tests         | pytest + pytest-qt (2 090+ tests)   |

---

## 2. Repository Layout

```
fam-market-manager/
├── fam/                          # Application package
│   ├── __init__.py               # __version__ = "1.9.8"
│   ├── app.py                    # Qt app entry, data dir, exception handler
│   ├── settings_io.py            # .fam file import/export
│   ├── database/
│   │   ├── connection.py         # Thread-local SQLite connection
│   │   ├── schema.py             # Table DDL + migrations (v1–v27)
│   │   ├── seed.py               # Opt-in sample data (via tutorial)
│   │   └── backup.py             # SQLite backup API + retention
│   ├── models/
│   │   ├── vendor.py             # Vendor CRUD
│   │   ├── market_day.py         # Market day lifecycle
│   │   ├── payment_method.py     # Payment method CRUD
│   │   ├── transaction.py        # Receipts + payment line items
│   │   ├── customer_order.py     # Multi-receipt customer orders
│   │   ├── fmnp.py               # FMNP check entries
│   │   ├── audit.py              # Append-only audit log
│   │   └── photo_hash.py         # SHA-256 hash lookups (Drive + local dedup)
│   ├── ui/
│   │   ├── main_window.py        # Sidebar nav + screen stack + backup timer + auto-update check
│   │   ├── market_day_screen.py  # Screen 0 — Open/close market day
│   │   ├── receipt_intake_screen.py  # Screen 1 — Add receipts
│   │   ├── payment_screen.py     # Screen 2 — Allocate payments + receipt printing
│   │   ├── fmnp_screen.py        # Screen 3 — FMNP entry
│   │   ├── admin_screen.py       # Screen 4 — Adjustments & voids
│   │   ├── reports_screen.py     # Screen 5 — Reports & exports
│   │   ├── settings_screen.py    # Screen 6 — Config + import/export + cloud sync + updates
│   │   ├── help_screen.py        # Screen 7 — Help (Walkthrough + Browse + Troubleshooting + System Status)
│   │   ├── help_walkthrough.py   # 5-stage animated walkthrough widget for the Help splash tab
│   │   ├── help_icons.py         # Custom flat-icon library (18 hand-painted pictograms via QPainter)
│   │   ├── tutorial_overlay.py   # Guided tutorial + auto-configure
│   │   ├── styles.py             # Global QSS + brand colours
│   │   ├── helpers.py            # Reusable widgets & helpers
│   │   └── widgets/
│   │       ├── payment_row.py    # Payment method entry row
│   │       └── summary_card.py   # Summary display cards
│   ├── help/                     # Structured in-app help library (no AI involvement)
│   │   ├── content.py            # Categories, articles, troubleshooting flows (single source of truth)
│   │   ├── search.py             # Ranked substring search across articles + flows
│   │   └── system_status.py      # Live diagnostic snapshot — never raises
│   ├── sync/
│   │   ├── base.py               # SyncResult dataclass
│   │   ├── manager.py            # SyncManager orchestration + agent tracker
│   │   ├── gsheets.py            # Google Sheets backend via gspread
│   │   ├── data_collector.py     # Collects report data + photo URLs for sync
│   │   ├── drive.py              # Google Drive photo upload (REST API) + tri-state verification + 10-min throttle
│   │   └── worker.py             # QThread worker for background sync
│   ├── update/
│   │   ├── checker.py            # GitHub API, version comparison, download, batch script + certifi-backed TLS context + zip-probe + pending-update marker
│   │   └── worker.py             # QThread workers for check + download
│   └── utils/
│       ├── app_settings.py       # Market code, device ID, sync/update settings, key-value store
│       ├── calculations.py       # Core financial math + charge/method_amount conversion + penny reconciliation
│       ├── money.py              # Integer-cents helpers: dollars_to_cents, cents_to_dollars, format_dollars
│       ├── export.py             # CSV export + ledger backup
│       ├── logging_config.py     # Rotating file logger
│       ├── photo_storage.py      # Photo copy/resize, SHA-256 hashing, local registry
│       └── photo_paths.py        # Multi-photo JSON encode/decode
├── tests/                            # 1857 tests across 33 files
│   ├── test_match_formula.py         # Formula validation, edge cases, real-world scenarios
│   ├── test_match_limit.py           # Daily cap logic, proportional reduction, penny reconciliation under cap
│   ├── test_returning_customer.py    # Multi-visit tracking
│   ├── test_adjustments.py           # Adjustments, voids, ledger
│   ├── test_fmnp_reports.py          # FMNP entries and reports
│   ├── test_fmnp_payment_method_toggle.py # FMNP-as-payment-method toggle (default inactive in v1.9.8+) and Entry-screen independence
│   ├── test_models.py                # Model CRUD operations
│   ├── test_market_code.py           # Market code, device ID
│   ├── test_backup.py                # Backup creation + retention
│   ├── test_schema.py                # Migrations, triggers, indexes
│   ├── test_settings_io.py           # Import/export round-trip
│   ├── test_sync.py                  # Cloud sync, data collection, FMNP dual-source, agent tracker
│   ├── test_sync_signal_coverage.py  # Every mutation path emits a sync trigger (FMNP delete, payment confirm, admin adjust/void, intake voids)
│   ├── test_update.py                # URL parsing, version comparison, update flow, runtime batch execution, certifi TLS context
│   ├── test_drive_verification.py    # VerifyResult tri-state, URL preservation on UNKNOWN, 10-min throttle
│   ├── test_charge_conversion.py     # Charge ↔ method_amount conversion
│   ├── test_auto_distribute.py       # Auto-distribute payment allocation, max-cap math, cap reconciliation
│   ├── test_denomination.py          # Denomination constraint validation
│   ├── test_multi_photo.py           # Multi-photo storage, encoding, drive upload
│   ├── test_cloud_sync_ux.py         # Sync UX, photo dedup, hash model, sync indicator state machine
│   ├── test_money_boundaries.py      # Integer-cents boundaries, FMNP check splitting, penny reconciliation
│   ├── test_reconciliation.py        # Three-way reconciliation (DB == Ledger == Sheets)
│   ├── test_ui_payment.py            # Payment screen UI: summary cards, multi-method, stepper, auto-distribute
│   ├── test_ui_workflows.py          # End-to-end market day simulation
│   ├── test_ui_guards.py             # Max-cap clamping, market day lifecycle guards, adjustment edge cases
│   ├── test_ui_expanded.py           # Production readiness E2E: payment confirm, draft save/resume, returning customer caps, void/adjustment propagation
│   ├── test_payment_method_safety.py # Payment method CRUD, deactivation safety, FMNP/FAM report separation
│   ├── test_help_content.py          # Help library structural integrity (article ids, cross-references, coverage canaries)
│   ├── test_help_walkthrough.py      # Walkthrough scene data + widget behavior + looping animation + Next-button flash
│   └── test_help_icons.py            # Custom flat-icon library (instantiation, paint output, scene cards)
├── releases/
│   └── (zip files on GitHub Releases)
├── requirements.txt
├── fam_manager.spec              # PyInstaller config
├── build.bat                     # Windows build script
└── PROJECT_INSTRUCTIONS.md       # ← This file
```

---

## 3. Critical Business Logic — Match Formula

The single most important calculation in the application. **Get this wrong and
every dollar amount in the system is incorrect.**

### Formula

```
match_amount = method_amount × match_percent / (100 + match_percent)
customer_charged = method_amount − match_amount
```

### Semantics

| Match % | Meaning     | $100 order: FAM pays | $100 order: Customer pays |
|---------|-------------|----------------------|---------------------------|
| 0%      | No match    | $0.00                | $100.00                   |
| 25%     | 1:4         | $20.00               | $80.00                    |
| 50%     | 1:2         | $33.33               | $66.67                    |
| 100%    | 1:1         | $50.00               | $50.00                    |
| 200%    | 2:1         | $66.67               | $33.33                    |
| 300%    | 3:1         | $75.00               | $25.00                    |

**Key properties:**
- `match_amount + customer_charged == method_amount` (always, per line item)
- `customer_total_paid + fam_subsidy_total == receipt_total` (when fully allocated)
- `customer_charged >= 0` for all valid inputs (match never exceeds receipt)
- All monetary values are **integer cents** (e.g. $89.99 = 8999). Rounding uses Python `round()` on integer arithmetic.
- Penny reconciliation: when a ±1¢ gap exists after allocation (rounding artifact from matched methods with odd-cent totals), the gap is absorbed into the FAM match of the largest matched line item. Customer charge is unchanged.

### Where the formula lives (4 locations — must stay in sync)

| File                           | Function / Line                  | Purpose                    |
|--------------------------------|----------------------------------|----------------------------|
| `fam/utils/calculations.py`    | `calculate_payment_breakdown()`  | Canonical calculation      |
| `fam/ui/widgets/payment_row.py`| `_recompute()`                   | Live UI preview            |
| `fam/ui/widgets/payment_row.py`| `get_data()`                     | Data collection for save   |
| `fam/ui/payment_screen.py`     | `_distribute_and_save_payments()`| Multi-receipt distribution |

### Charge ↔ Method Amount Conversion (v1.8.0)

Payment rows now accept **customer charge** (what the customer pays) instead of total allocation.
Two conversion functions in `calculations.py` bridge the input to the existing formula:

```
charge_to_method_amount(charge, match_percent) → charge × (1 + match_percent / 100)
method_amount_to_charge(method_amount, match_percent) → method_amount / (1 + match_percent / 100)
```

### Daily match limit (cap)

Markets can set a per-customer daily FAM match cap (e.g. $100/day).
When a customer's total match exceeds the cap, all match amounts are
**proportionally reduced** so the total equals the cap exactly.

---

## 4. Database Schema (v22)

### Tables

**markets** — name, address, daily_match_limit, match_limit_active, is_active

**vendors** — name, contact_info, is_active

**market_vendors** — junction table (market_id, vendor_id)

**payment_methods** — name, match_percent (0–999), sort_order, denomination, photo_required, is_active

**market_payment_methods** — junction table (market_id, payment_method_id)

**market_days** — market_id, date, status (Open/Closed), opened_by, closed_by

**customer_orders** — market_day_id, customer_label, zip_code, status

**transactions** — fam_transaction_id (FAM-{CODE}-YYYYMMDD-NNNN), market_day_id, vendor_id, receipt_total, status, customer_order_id

**payment_line_items** — transaction_id, payment_method_id, method_name_snapshot, match_percent_snapshot, method_amount, match_amount, customer_charged, photo_path, photo_drive_url

**fmnp_entries** — market_day_id, vendor_id, amount, check_count, photo_path, photo_drive_url, status (Active/Deleted), entered_by

**audit_log** — table_name, record_id, action, field_name, old_value, new_value, reason_code, notes, changed_by, app_version, device_id

**app_settings** — key-value store (market_code, device_id, tutorial_shown, large_receipt_threshold, sync_credentials_loaded, sync_spreadsheet_id, sync_drive_folder_id, last_sync_at, last_sync_error, update_repo_url, update_auto_check, update_last_check, update_last_version, update_dismissed_version)

**photo_hashes** — content_hash (PK) → drive_url (Drive upload dedup, persists across sync cycles)

**local_photo_hashes** — content_hash (PK) → relative_path (cross-transaction UI attachment dedup)

**schema_version** — version (current: 34), applied_at — UNIQUE INDEX on (version) added in v34

### Migration History

| Version | Change |
|---------|--------|
| v1→v2 | Added customer_orders table |
| v2→v3 | Added market_vendors junction table |
| v3→v4 | Added validation triggers + performance indexes |
| v4→v5 | Added daily_match_limit columns to markets |
| v5→v6 | Renamed discount → match; expanded match_percent to 0–999 |
| v6→v7 | Added zip_code to customer_orders |
| v7→v8 | Added FMNP payment method (100% match) |
| v8→v9 | Added market_payment_methods junction table |
| v9→v10 | Added app_settings key-value table |
| v10→v11 | Added status column to fmnp_entries for soft-delete |
| v11→v12 | Added denomination column to payment_methods |
| v12→v13 | Added photo_path + photo_drive_url to fmnp_entries |
| v13→v14 | Added photo_required to payment_methods, photo_path to payment_line_items |
| v14→v15 | Added photo_drive_url to payment_line_items |
| v15→v16 | Added app_version + device_id to audit_log |
| v16→v17 | Added photo_hashes table (Drive upload content-hash dedup) |
| v17→v18 | Added local_photo_hashes table (cross-transaction UI dedup) + backfill |
| v18→v19 | Added drive_photos_folder_id to app_settings; photo subfolder structure |
| v19→v20 | Added entered_by to fmnp_entries; FMNP entry audit fields |
| v20→v21 | Added performance indexes: transactions(customer_order_id), market_days(market_id, date), audit_log(table_name, record_id) |
| v21→v22 | Converted all monetary REAL columns to INTEGER cents (markets.daily_match_limit, payment_methods.denomination, transactions.receipt_total, payment_line_items.method_amount/match_amount/customer_charged, fmnp_entries.amount). Uses ROUND() before CAST to avoid float truncation. |
| v22→v23 | Enforced UNIQUE on vendors.name (matching markets and payment_methods).  Existing duplicate vendor names are auto-renamed with " (2)", " (3)" suffixes on the higher-id rows so vendor IDs and every FK relationship stay intact; the older record keeps the canonical name.  Implemented as a UNIQUE INDEX (`idx_vendors_name_unique`) since SQLite cannot add a UNIQUE column constraint via ALTER TABLE. |
| v23→v24 | Added `vendor_payment_methods` junction (vendor_id, payment_method_id, UNIQUE).  Permissive backfill: every existing vendor inherits every payment method so no flow breaks on first launch; coordinators tighten eligibility per-vendor via Settings → Vendors → Methods.  Drives the per-row vendor dropdown for denominated payments on the Payment screen, so denominated instruments commit to a single bound vendor's transaction instead of being proportionally spread. |

---

## 5. Multi-Market Device Identity

### Market Code
- 1–4 uppercase alpha chars, auto-derived from market name on open/reopen
- Multi-word names: first letter of each word (e.g., "Bethel Park Farmers Market" → `BPFM`)
- Single-word names: first 2 alpha characters
- Stored in `app_settings` table (key: `market_code`)
- Embedded in: transaction IDs, CSV filenames, ledger header, receipt printouts, title bar

### Device ID
- Windows `MachineGuid` from `HKLM\SOFTWARE\Microsoft\Cryptography`
- Fallback: `hostname-{platform.node()}`
- Captured on every app launch, stored in `app_settings` (key: `device_id`)
- Appears in: CSV export columns, ledger header, settings export header

### CSV Identity Columns
All CSV exports inject `market_code` and `device_id` as the first two columns.

---

## 6. Backup System

### Database Backups (`fam/database/backup.py`)
- SQLite backup API for consistent hot copies (works with WAL mode)
- Stored in `{data_dir}/backups/`
- Retention: 20 most recent files, older auto-deleted
- Created on: market open, market close, every 5 minutes (auto timer)
- Never raises — all errors logged silently

### Ledger Backup (`fam/utils/export.py`)
- Single file: `fam_ledger_backup.txt`
- Contains all transactions from entire database
- Atomic write: tempfile + os.replace
- Updated after: payment confirmation, adjustment, void, market-day close

---

## 7. First-Run Experience

1. App starts with clean slate (no pre-loaded data)
2. Tutorial auto-launches on first run (11 steps)
3. Final step: "Quick Setup" with Yes/No buttons
4. "Yes" calls `seed_sample_data()` → loads 3 markets, 23 vendors, 6 payment methods
5. "No" → blank app, user configures manually or imports `.fam` file
6. `tutorial_shown` flag persisted in `app_settings` to prevent re-launch
7. Tutorial can be re-run anytime via "Start Tutorial" button

---

## 8. Application Screens

### Screen 0 — Market Day Setup
- Select market, enter volunteer name, open/close/reopen market day
- Auto-derives market code from market name on open
- Signal: `market_day_changed`

### Screen 1 — Receipt Intake
- New/returning customer, vendor dropdown, receipt total, zip code
- Multiple receipts per customer, pending orders
- Signal: `customer_order_ready(int)`

### Screen 2 — Payment Processing
- Summary cards, dynamic payment rows, daily match limit display
- Charge-based input — operator enters customer charge, system computes match + total
- Denomination validation for denominated methods (e.g. FMNP $20 increments)
- Photo receipt attachment per payment method (single or multi-photo for denominated)
- Confirm payment → generates transaction IDs (FAM-{CODE}-YYYYMMDD-NNNN)
- Print receipt after confirmation
- Double-click protection on confirm button
- Signals: `payment_confirmed()`, `draft_saved()`

### Screen 3 — FMNP Entry
- Market day + vendor + amount + check count
- Multi-photo attachment — dynamic check photo slots based on amount ÷ denomination
- Scrollable photo slot area for large check volumes (fixed 160px, scrolls when >3 rows)
- Photo dedup: within-entry (hard block) + cross-entry (warning with override)
- Edit/delete with soft-delete (status: Active/Deleted)

### Screen 4 — Admin Adjustments
- Search/filter transactions, adjust amounts/vendors, void
- Audit log with reason codes

### Screen 5 — Reports
- Summary, Detailed Ledger, Vendor Reimbursement, FAM Match, Transaction Log, Activity Log, Geolocation, Charts, Error Log
- All report table columns auto-fit to content and are manually resizable (drag to adjust)
- CSV export with market code in filenames and identity columns

### Screen 6 — Settings
- Markets, Vendors, Payment Methods, Preferences tabs
- Import/Export `.fam` settings files with preview dialog
- Device Identity display (read-only market code + device ID)
- Cloud Sync tab — One-way sync to Google Sheets (credentials, spreadsheet ID, sync now)
- Updates tab — GitHub repo URL, check for updates, download & install, auto-check toggle
- **FMNP payment method is togglable** as of v1.9.8.  Defaults to inactive on fresh install / Load Defaults.  When inactive it does NOT appear as a payment-row option on the Payment Screen, but the FMNP Entry screen continues to function regardless (it looks up the FMNP method by name without filtering on `is_active`).  Coordinators activate FMNP for the Payment Screen only when their market wants in-app FMNP-as-payment-method (rare — most markets use the FMNP Entry screen exclusively for vendor-matched FMNP).

### Screen 7 — Help
- **Four tabs**, in order: **Walkthrough** (default — animated training overview), **Browse** (categorized articles + live search), **Troubleshooting** (symptom-based decision-tree flows), **System Status** (live diagnostic snapshot with **Copy Diagnostic Info** button)
- Walkthrough auto-plays on first activation per session.  Each of the 5 stages loops its animation in place; the volunteer clicks **Next** when ready (the button pulses gold after the first iteration finishes).  Pause / Prev / Restart / Skip Tour controls.
- Walkthrough scenes are composed from `SceneCard` widgets containing `FlatIcon` pictograms — 18 hand-painted icons in `fam/ui/help_icons.py` (Person, VendorStall, Receipt, Laptop, Card, Check, Cash, Runner, Box, Stamp, Cloud, File, Envelope, Manager, Clipboard, Table, Arrow, Checkmark).  All vector via QPainter — crisp at any DPI, FAM brand colors, no external dependencies.
- Content lives in `fam/help/content.py` as structured Python data (Categories, Articles, TroubleshootingFlows).  v1.9.9 ships **52 articles across 8 categories** and **10 troubleshooting flows**.
- Article bodies are Markdown rendered to HTML by Qt's `QTextBrowser` via a small in-house renderer in `fam/ui/help_screen.py`
- Search ranks title hits over body hits via `fam/help/search.py`
- System Status pulls from `fam/help/system_status.py`'s `collect_status()` — never raises, safe to call any time.  Reports app version, sync state, disk usage (DB / photos / backups / log / ledger backup), record counts (transactions, FMNP, audit log).  Copy Diagnostic Info button serializes the snapshot for paste into a coordinator email.
- **No AI / LLM involvement** — all answers are curated text written by the engineer who shipped the corresponding code change

---

## 8a. Help Content Discipline

The Help screen is the volunteer's first stop when something is unclear.
Stale or missing help content erodes their trust in the system more than
the underlying defect it's trying to explain.

**The rule:** any change to the user-facing surface MUST update the
matching article in `fam/help/content.py` in the **same commit**.

User-facing surface includes:

- New screens, tabs, or top-level navigation items
- New buttons, dropdowns, or input controls volunteers will see
- Changes to existing button labels or workflow ordering
- New error conditions / dialogs / warnings
- New sync states or indicator labels
- Changes to default behavior (e.g. v1.9.8 making FMNP inactive by default)
- Changes to where data is stored on disk
- Changes to backup retention policy
- New file types in the data directory

For each change, ask: *what would a volunteer in front of this for the
first time need to know?* Then update or add the matching article.

### Mechanical guards (`tests/test_help_content.py`)

The test suite enforces structural correctness:

- Every article has required fields (id, title, body, category)
- All `related_articles` cross-references resolve to real article ids
- No duplicate ids across articles or troubleshooting flows
- Every category has at least one article
- Coverage canaries: required articles for FMNP dual-path, sync,
  market lifecycle, corrections, and data location must always exist
- Source-level guards that the Help screen is registered in
  `main_window.py` nav and stack

### What the tests do NOT catch

- "You added a feature but forgot to write a help article" — that's
  a human discipline failure, not a structural one
- Out-of-date prose ("v1.7.0 added X" still in the article when X is
  long since reworked)
- Confusing writing or wrong information

These require human review at edit time. When merging a PR that
touches UI, the reviewer's checklist should include "did the help
content get updated?"

### Authoring conventions

- **Article id**: kebab-case slug (e.g. `fmnp-via-tracking`)
- **Body**: Markdown subset — `## headings`, `**bold**`, `*italic*`,
  `` `inline code` ``, `- bullets`, `| tables |`, ```` ``` code blocks ``` ````
- **Keywords**: lowercase terms a volunteer might type into search
- **Related articles**: 2-5 cross-references max — too many becomes noise
- **Tone**: direct and action-oriented. Lead with what to do, then why.
- **Length**: 100-2000 chars per article. Stub articles (under 100
  chars) fail the structural test.

### Troubleshooting flows

For symptom-based help (e.g. "sync indicator is red"), use a
`TroubleshootingFlow` instead of an article. Format:

- `id` starts with `ts-`
- `title` phrased as the symptom (the volunteer's own words)
- `symptom` is a one-line restatement
- `steps` is an ordered list — each step a single concrete action
- Cross-reference articles via `related_articles` for deeper reading

---

## 9. Test Suite

**Run:** `python -m pytest tests/ -v` from project root

**2 090 total tests across 40+ files** — all must pass before committing.

**Before tagging any release**, run the full Release Audit Gate
(`scripts\run_release_audit.bat`) — pytest is gate 1 of 3.  See §18.

### v1.9.9 audit additions

The April 2026 production-readiness audit added three test files
that are now part of the standing release gate:

* `tests/test_production_stress.py` — 15 stress tests including a
  10-vendor mega-order, returning-customer match-cap accumulation,
  5-iteration adjustment chain, adjust→void integrity, penny
  reconciliation, and edge-case discovery
* `tests/test_audit_coverage_gaps.py` — 10 tests pinning logged
  surfaces (regression alarm) and documenting CRUD-on-settings
  audit gaps (forward-progress alarm)
* `tests/test_export_reconciliation.py` — 8 tests proving every
  CSV export reconciles to the underlying database to ±0¢

**Do not silently bypass any of these.** If a future change makes
one impossible to satisfy, raise it as a discussion before
disabling — the contract being protected is "no penny lost between
the database and a vendor reimbursement check."

---

## 10. Build & Deployment

### Development
```bash
pip install -r requirements.txt
python run.py
```

### Windows Executable
```bash
build.bat
# Output: dist/FAM Manager/FAM Manager.exe
```

### Data Persistence
All data stored in `%APPDATA%\FAM Market Manager\` (separate from exe):
- `fam_data.db` — SQLite database (all app data)
- `fam_ledger_backup.txt` — human-readable ledger backup
- `fam_manager.log` — rotating log file
- `sync_credentials.json` — Google Sheets/Drive credentials (if configured)
- `photos/` — locally stored check/receipt photos (resized to ≤1920px)
- `backups/` — automatic database backups (20 most recent)
- `_update_backup/` — previous app version (created during auto-update)

Upgrading: use in-app auto-update or manually replace app folder. Schema migrations run automatically.
Legacy data (v1.5.1 and earlier) auto-migrated from exe directory on first launch.

---

## 11. Version History

| Version | Date       | Summary |
|---------|------------|---------|
| v2.0.7  | 2026-05-07 (in flight) | **Hotfix release covering post-v2.0.6 onsite findings.**  Schema v34 → **v35** (`_migrate_v34_to_v35` backfills universal SNAP/Cash bindings; idempotent).  **Headline policy: SNAP and Cash are now universally accepted at every vendor.**  `is_universal_vendor_method(name)` helper, `unassign_payment_method_from_vendor` refuses for SNAP/Cash with a WARN log, `VendorEligiblePaymentMethodsDialog` checkboxes for those two methods are checked + disabled + tooltipped.  Eliminates the silent-SNAP-onto-ineligible-vendor reproducer class.  **Critical fixes:** (1) **Denomination preservation through adjustments** — engine snap-back at the end of `resolve_payment_state` rounds denom rows' `customer_charged` to denomination multiples; save-layer guard in `save_payment_line_items` re-validates and snaps before write; closes the `$0.47 Food Bucks` regression class.  (2) **Adjustment safety gate for denominated transactions** — `AdminScreen._adjust_transaction` now opens a Void-Instead/Adjust-Anyway/Cancel dialog when the txn contains denom methods; `ADJUST_OVERRIDE` audit log entry records the override path; sibling-transaction warning when applicable.  (3) **Single-vendor multi-receipt allocation fix** — Layer 2C and `_distribute_and_save_payments` Phase 1 now treat single-vendor orders with multiple receipts as a unit (proportional distribution to per-receipt remaining capacity); honors the implicit binding when `bound_vendor_id` is None on a single-vendor order.  (4) **Vendor Reimbursement after voids** — aggregation correctly excludes voided lines so the surviving receipts on a partially-voided multi-receipt order roll up correctly.  (5) **Photo dedup cache invalidation** — `void_transaction` calls `cleanup_orphaned_hashes_for_transaction`; `delete_fmnp_entry` calls `cleanup_orphaned_hashes_for_fmnp`; `update_fmnp_entry` clears `photo_drive_url` when `photo_path` changes so re-uploads pick up a fresh Drive URL.  **Defensive hardening:** (a) **Cap-bound impossible-to-balance recommendation** — Layer 2A's "Payment row mismatch" guard now detects the cap-bound + denom + non-denom-overshoot pattern (`match_was_capped=True` + spinbox > engine `customer_charged` + `denomination > 0` row exists) and surfaces an enriched dialog naming the cap as the root cause, stating the exact gap to reduce, and **recommending splitting the customer's receipts into separate orders one method at a time** (the user's preferred resolution after an attempted `_auto_rebalance_non_denom` was reverted because it fought the engine's deterministic Path B + Pass 4).  Logged as "Cap-bound impossible-to-balance scenario".  Generic Layer 2A message preserved for non-cap-bound mismatches.  (b) **Layer 2B eligibility-blamed gate** — `overshoot > 1 AND ineligible_vendor_names` required before firing the "X cannot accept SNAP" message; with universal SNAP/Cash binding the over-allocation case falls through to Layer 2C's accurate per-receipt message.  (c) **Save-path per-vendor method eligibility filter** — Phase 2 of `_distribute_and_save_payments` now filters non-denom target transactions by `get_vendor_payment_method_ids(vendor_id)` before splitting customer charge; matches Layer 2C's simulation so save and validation stay in lock-step.  (d) **Denomination-aware Auto-Distribute** — seed-row pass respects per-vendor method eligibility for denominated methods at the moment it picks the bound vendor.  **In-app help additions:** new article `split-orders-when-stuck` and troubleshooting flow `ts-payment-screen-hard-block` document the split-into-separate-orders workaround as the durable resolution for cap-bound scenarios; existing `ts-cap-warning-wrong` and `ts-adjustment-blocked-by-mismatch` flows updated to cross-reference.  **Reverted (do NOT re-introduce):** `PaymentScreen._auto_rebalance_non_denom` — fought the engine deterministically; pinned absent in `tests/test_cap_bound_split_recommendation.py::TestRevertedAutoRebalance`.  Same revert removed the `denom_quantity_changed` Signal and the dedicated stepper hookup on `PaymentRow` (no consumer).  **3,504 tests passing** across 65+ files (was 3,387 in v2.0.6); 39 skipped, 1 xfailed.  New test files: `test_universal_vendor_method_bindings.py`, `test_adjustment_denom_safety_gate.py`, `test_save_layer_denom_guard.py`, `test_denom_preservation_in_adjustments.py`, `test_single_vendor_multi_receipt_layer2c.py`, `test_save_path_single_vendor_multi_receipt.py`, `test_non_denom_per_vendor_eligibility.py`, `test_non_denom_capacity_check.py`, `test_layer_2b_gate_no_ineligible.py`, `test_auto_distribute_per_vendor_eligibility.py`, `test_seed_default_food_rx_and_rewards.py`, `test_cap_bound_split_recommendation.py`.  Updated `tests/test_audit_coverage_gaps.py::test_vendor_method_eligibility_logged` to use Food Bucks (id=2) instead of SNAP since SNAP is now universal and unassign is refused.  See `RELEASE_NOTES_v2.0.7.md`. |
| v2.0.5  | 2026-05-05 | **User-reported fix: Error Log Sheets traceback.**  In the synced Google Sheet, CRITICAL entries showed only the first line (`Unhandled exception:`) — the multi-line traceback was either in a separate `Traceback` column the coordinator didn't notice or missing entirely.  Local Reports → Error Log detail panel had always shown the full content (Time / Level / Area / Module / Message / Traceback) by stitching `e['message']` + `e['traceback']` together; the cloud Sheet didn't.  Fix: `_collect_error_log` (`fam/sync/data_collector.py:820-870`) now embeds `f"{first_line}\n\nTraceback:\n{tb}"` into the `Message` column when a traceback is present, so the Sheet matches the local detail-panel format.  The separate `Traceback` column is preserved for backward compatibility with existing dashboards / filters.  `SyncManager.SHEET_KEYS['Error Log']` changed from `[market_code, device_id, Timestamp, Module, Message]` to `[market_code, device_id, Timestamp, Module, Level]` — Message is now multi-KB so including it in the composite key was wasteful and brittle against newline normalisation.  Two errors landing in the same second from the same module at the same level are the same event by every operational definition; using Timestamp+Module+Level is sufficient dedup.  Existing Sheets with the old key get their rows refreshed on the next sync via the `WHOLE_DATASET_TABS` `delete_stale=True` semantics.  4 new regression tests in `test_error_log_full_traceback_in_message.py`: CRITICAL with traceback embeds full content, WARNING without traceback unchanged, end-to-end Sheets cell value contains all required fragments, composite key uses Level not Message.  Updated `test_sync.py::test_error_log_key_columns` to match the new key.  3,273 tests passing.  **Note on the user-reported UnboundLocalError**: the v2.0.1 fix in `_adjust_transaction` is still in place (regression test `test_adjust_transaction_no_local_shadow.py` passes).  Users hitting this error are running a build that pre-dates v2.0.1 — rebuild the .exe from current source to resolve. |
| v2.0.4  | 2026-05-05 | **Ship-and-forget release.**  Bundles v2.0.3 security fixes plus year-2 / multi-market scale hardening.  This is the last planned release; remaining items are documented as known limitations rather than fixed.  **HIGH-1 gsheets payload chunking** — `update_cells` and `append_rows` now chunk to 5000 cells / 1000 rows per API call.  Pre-fix, after a long offline period at year-2 scale (50K+ dirty cells across the transactions tab is realistic), a manual full sync would exceed the Sheets ~10K-cell-per-call limit and the (correctly) refusing 4xx-no-retry logic would leave "Sync failed" with no path forward.  **HIGH-2 per-market backup retention** — replaces global "newest 20 wins" with `BACKUP_RETENTION_COUNT_PER_MARKET=20` bucketed by market_code.  Multi-market deployments running Market A weekly + Market B monthly no longer evict Market B's backups within ~10 weeks.  Buckets sort by the embedded timestamp segment via a regex matcher (`_BACKUP_FILENAME_RE`) that handles both legacy second-resolution and new microsecond filenames.  **HIGH-5 microsecond backup timestamps** — `eastern_now().strftime("%Y%m%d_%H%M%S") + "_{microsecond:06d}"`.  Two backups landing in the same wall-clock second no longer silently overwrite each other.  **HIGH-6 SQLite PRAGMAs** — added `PRAGMA synchronous=NORMAL` (the standard WAL-mode recommendation; durable across crash, ~4× faster on Windows than FULL) and `PRAGMA wal_autocheckpoint=500` (cap the WAL at ~2MB so restart recovery is fast and `Connection.backup` snapshots a small WAL tail).  **HIGH-SEC-2 mask Sheet/Drive IDs in diagnostic** — pre-fix the Copy Diagnostic Info clipboard exposed full `sync_spreadsheet_id` and `drive_folder_id` verbatim; pasted into a chat run by an attacker, those enabled targeted social-engineering.  Now masked via `_mask_id` (4-char prefix + suffix, ellipsis middle).  **MED-SEC-1 explicit https:// requirement** — `_is_allowed_repo_url` now rejects `http://` URLs even when owner/repo would parse to the allow-listed pair.  Defense-in-depth against any future code path reusing the saved URL for a fetch.  **MED-SEC-2 / MED-SEC-5 html.escape diagnostic** — `help_screen._refresh_status` switched from `.replace('<','&lt;').replace('>','&gt;')` (only `<>`) to `html.escape(text, quote=True)` (escapes `& < > " '`).  Defense against vendor-name-with-HTML-payload landing in the diagnostic clipboard and re-rendering in a downstream chat client.  **MED-SEC-3 startup temp-dir cleanup** — `fam/app.py:run` now `shutil.rmtree`s `_update_temp/` and `_update_download/` from `data_dir` on startup.  Pre-fix, an interrupted update left these dirs around indefinitely as wasted disk and (combined with the v2.0.3 backup hash-pin defense) a reduced-but-nonzero attacker prep window.  **TEST coverage** — new IDN/homoglyph (Cyrillic 'е' in `seansaball`), path-traversal-in-URL, userinfo-in-URL, and explicit `http://` rejection tests in `test_update_repo_allowlist.py`.  Updated `test_market_code.py::test_retention_per_market_does_not_starve` to verify the new per-market semantics (high-volume market trims to 20, low-volume market keeps all 3).  3,269 tests passing across 41 test files.  **Known limitations explicitly NOT fixed:** HIGH-3 (customer_label same-device race — would require schema-version bump or retry-on-IntegrityError logic, both with regression risk), HIGH-4 (export filter inconsistency between ledger backup / sync / FMNP-context — changing user-visible numbers at release-time would break coordinator mental models), HIGH-7 (zero a11y annotations — huge surface area for regression), MED-SEC-4 (sub-frame TOCTOU between C5 re-check and Popen — would require DB-level mutex), F-H2 Pass-4 give-back (deferred since v2.0.1, customer-side Phase B forfeit over-credit). |
| v2.0.3  | 2026-05-05 | **Second-pass security + scale hotfix.**  Found by a four-agent post-v2.0.2 review.  Two CRITICAL security findings: **CRIT-SEC-1** the v2.0.2 ``_update_backup`` auto-rollback (B-H5) introduced a regression — an attacker with FS write to ``%APPDATA%`` could plant a malicious ``FAM Manager.exe`` in ``_update_backup\`` and wait for any update failure to trigger silent restore into the install dir (data-dir-write → install-dir-RCE).  Defense: SHA-256 manifest written to ``app_dir\_update_manifest.sha256`` at backup time (install-dir trust boundary, not attacker-writable in threat model).  Rollback now PowerShell-verifies the backup's ``FAM Manager.exe`` hash against the trusted manifest BEFORE copying.  Mismatch ⇒ refuse rollback, emit "SECURITY WARNING: Backup hash MISMATCH" with manual-recovery instructions. Refactored install-block goto-rollback flow to use ``:ROLLBACK_AND_EXIT`` label instead of inline rollback inside parenthesised ``if (...)`` blocks (batch syntax forbids ``goto`` labels inside ``(...)``).  **CRIT-SEC-2** photo path traversal: ``get_photo_full_path`` did ``os.path.join(data_dir, relative_path)`` which returns the absolute path verbatim when ``relative_path`` is absolute.  An attacker writing ``photo_path='C:\\Users\\X\\.aws\\credentials'`` to a ``payment_line_items`` or ``fmnp_entries`` row caused the next Drive sync to upload that file to the volunteer's Drive — arbitrary-file exfiltration.  Defense: new ``_validate_relative_photo_path`` rejects absolute paths, drive letters, ``..`` escapes, and any normalised path that lands outside ``data_dir``.  ``photo_exists`` returns False rather than raising.  ``get_photo_full_path`` raises ``UnsafePhotoPathError``.  Drive uploader explicitly logs and skips unsafe paths.  Two NEW-CRIT scale/lifecycle issues: **NEW-CRIT-1** ``MainWindow.closeEvent`` did not wait for ``settings_screen._update_dl_thread`` — closing mid-download left an orphan QThread that fired ``_on_download_finished`` against a destroyed parent (uncaught C++ exception / zombie process).  Now closeEvent waits up to 10s with terminate fallback.  **NEW-CRIT-2** ``ReportsScreen._load_activity_log`` was unbounded — at year 2-3 scale (500K+ ``audit_log`` rows) opening Reports → Activity Log froze the UI thread for 30-60s while ``fetchall`` materialised every row.  Now uses ``LIMIT 1000`` plus ``ORDER BY changed_at DESC, id DESC``.  Plus 23 new regression tests in ``tests/test_v2_0_3_regression_coverage.py`` covering the seven TEST-CRIT gaps the v2.0.2 audit identified: F-H1 dict-level penny-rec consistency, UF-H6 audit no-double-emit, UF-H10 rewards rollback, DB-H2 RuntimeError propagation, UI-H8 cap re-check, UF-H1/H2 void rollback, B-H8 hostname fallback, plus seven CRIT-SEC-2 path-traversal cases and a NEW-CRIT-2 source-pin.  3,264 tests passing across 40+ test files (no regressions). |
| v2.0.2  | 2026-05-05 | **Pre-release security + observability hotfix.**  Schema v33 → v34.  Five CRITICAL ship-blockers, eight HIGH-severity hardening items.  **C1 (cloud):** ``gsheets._retry_on_error`` 4xx guard mirrors the v2.0.1 ``drive.py`` fix — ``requests.HTTPError`` is-a ``OSError`` was retrying permanent 400/401/403/404 from Sheets five times across nine tabs (~80s wasted per failed call).  **C2 (DB):** fresh-install branch in ``initialize_database`` now runs ``_migrate_v32_to_v33``; brand-new v2.0.1+ deployments were stamping ``schema_version=33`` without the ``chk_pli_uf_zero_*`` triggers.  **C3 (logging):** Reports → Error Log "Errors Only" filter now includes CRITICAL — pre-fix it dropped every unhandled-exception entry, defeating the v2.0.1 ``_global_exception_handler`` work entirely.  **C4 (security):** ``set_update_repo_url`` enforces an allow-list pinned to ``seansaball/fam-market-manager``; ``get_update_repo_url`` ignores any non-allow-listed value as a defense-in-depth read guard; ``_download_and_install`` validates the saved URL before download.  Closes a one-shot RCE-as-installer where a tampered ``.fam`` import or rogue Sheets-synced setting could redirect the auto-update channel.  **C5 (UX):** ``_on_download_finished`` re-checks ``get_open_market_day()`` immediately before launching the install script — closes the TOCTOU window the v2.0.1 pre-download guard left open (download takes 30s–min; user can open a market mid-download).  **DB-C2 (forensics):** new schema v34 migration deduplicates pre-existing ``schema_version`` rows and adds ``CREATE UNIQUE INDEX idx_schema_version_unique ON schema_version (version)`` so future Reset cycles can't accumulate duplicate rows.  **F-H1:** penny-reconciliation negative-match-guard branch now recomputes ``customer_total_paid`` after the in-place mutation, eliminating a 1¢ drift that surfaced as ``is_valid=False`` in the summary card.  **F-H3:** ``ACTION_LABELS`` now includes ``UNALLOCATED_FUNDS``, ``AUTO_CLOSE``, and ``REWARD_ISSUED`` so the Activity Log dropdown can filter them; data was always logged correctly, only the UX selector was missing.  **UF-H6:** ``AdjustmentDialog`` save passes ``_skip_audit=True`` to all three ``update_transaction`` calls; pre-fix every adjust produced 2-4 audit rows per changed field instead of 1, polluting Activity Log + audit_log table-scan health at scale.  **UF-H10:** confirm path no longer swallows ``record_generated_rewards`` exceptions — a failed reward insert now triggers ``conn.rollback()`` + a "Payment failed: please retry" message BEFORE the clerk hands over physical tokens.  **DB-H2:** pre-migration backup failure is now FATAL for the migration step; pre-fix a logger.warning + continue let destructive migrations like v21→v22 run with no rollback artifact.  **UI-H8:** ``AdjustmentDialog`` accept re-checks the customer's daily match cap with ``_recompute_match_limit_for_txn``; pre-fix the cap was snapshotted at dialog open and a concurrent confirmation on another laptop could exceed the daily limit.  **UF-H4 / UF-H5:** AdminScreen and FMNPScreen filter selections preserve across ``refresh()`` / ``data_changed`` signals; FMNPScreen ``_has_in_progress_edit()`` guard prevents losing mid-entry photo state when the volunteer briefly navigates away.  **UF-H1 / UF-H2:** ``void_transaction`` gains ``commit=False`` so AdminScreen ``_void_transaction`` and ReceiptIntake ``_remove_receipt`` bundle the void + parent-customer-order status flip into a single atomic commit — pre-fix a transient ``database is locked`` between them left the txn voided but the order still Confirmed/Draft.  **B-H8:** ``capture_device_id`` synthetic ``hostname-XXX`` fallback is treated as not-a-device-id by ``get_device_id`` and the v1.9.10 startup hard-fail; image-cloned fleet laptops sharing a hostname were silently colliding on ``device_id`` and corrupting cross-device cloud sync.  **B-H5:** auto-update batch script implements automatic rollback from ``_update_backup`` on xcopy / Expand-Archive failure; pre-fix the user was left with a half-overwritten install dir and had to copy the backup back via File Explorer.  Also ``-ErrorAction Stop`` on Expand-Archive surfaces non-terminating errors as non-zero exit codes.  3,241 tests passing across 39 test files (no regressions; new files: ``test_gsheets_4xx_retry.py``, ``test_fresh_install_v33_v34.py``, ``test_error_log_filter_includes_critical.py``, ``test_update_repo_allowlist.py``, ``test_update_install_toctou.py``).  Eight parallel research agents conducted the pre-release production-readiness audit.  No code-signing yet (Defender SmartScreen warnings still expected); FMNP method must not be renamed in Settings (Reports + sync collector key off the literal string ``'FMNP'``).  Vendor Reimbursement does NOT auto-merge across devices by design — coordinator workflow gap documented for future v2.1+ work. |
| v2.0.1  | 2026-05-04 | **v2.0.1 hardening pass.**  10 critical/high fixes from the parallel research-agent production audit (Tier 1) plus 11 high-value hardening items (Tier 2).  Schema v32 → v33.  Highlights: ``InstanceLock`` cross-platform byte-range lock replacing kernel mutex; pending-update marker check moved before ``window.show()``; ``_safe_instance_lock_state`` ctypes-based PID liveness (replaces tasklist subprocess + console-window flash); System Status ``_safe_count`` returns -1 sentinel for OperationalError; ``void_customer_order`` per-txn VOID audit emission via ``update_transaction(_skip_audit=True)`` + per-txn VOID ``log_action``; ``.fam`` import/export round-trip preserves ``is_active`` + ``photo_required``; ``upsert_rows`` gains ``delete_stale`` parameter for narrow-scope sync (``WHOLE_DATASET_TABS = frozenset({'Vendor Reimbursement', 'Error Log'})`` always-prune); ``data_collector`` snapshot isolation via single BEGIN/COMMIT; FMNP Source B uses ``customer_charged`` (face value) not ``method_amount``; Vendor Reimbursement uses ``all_md_ids`` regardless of narrow scope; Drive ``_escape_drive_query_string`` helper for apostrophe-in-folder-name; ``_drive_retry`` 4xx guard (HTTPError is-a OSError); ``AdjustmentDialog`` re-fetches ``get_transaction_by_id(txn['id'])`` on accept (no local-import shadow); ``main_window`` auto-update enhancements (``_AUTO_CHECK_COOLDOWN_HOURS=6``, snooze=6h, cache-replay popup, ``write_pending_update_marker`` atomic via tempfile + os.replace + fsync); PaymentScreen ``_update_summary`` re-entry guard with ``_in_update_summary`` flag; ``ReceiptIntake._remove_receipt`` cascades void to parent customer_order; ``ReportsScreen._generate_reports`` snapshot-isolated; FMNP summary tile sums Path 1 + Path 2; Generated Rewards banner reworded; SettingsScreen Reset path with typed-RESET QInputDialog + pre-reset .bak via ``sqlite3.Connection.backup``; ``_add_market`` explicit ``daily_match_limit=10000`` (defends against legacy column DEFAULT); ``log_reader.py`` default level set ``{'CRITICAL', 'ERROR', 'WARNING'}``; ``logging_config.py`` handler attached to ROOT logger (not ``fam``) + ``fam.propagate=True`` + ``logging.captureWarnings(True)``; ``cleanup_uploaded_local_photos`` removes orphan ``local_photo_hashes`` rows.  Schema v32→v33: ``chk_pli_uf_zero_insert/update`` triggers enforce ``customer_charged=0`` AND ``match_amount=0`` on ``method_name_snapshot='Unallocated Funds'`` rows.  3,185 tests passing across 38+ test files. |
| v1.9.9  | 2026-04-29 | **Large onsite-findings bundle.**  Schema v23 → v27.  16+ feature areas, ~545 new tests.  Highlights below; the full list of new test files is in §0.  **Per-vendor binding architecture (schema v24):** `vendor_payment_methods` junction table with permissive migration backfill.  PaymentRow gains an inline vendor dropdown next to the method combo for denominated methods on multi-vendor orders; denominated rows can be added multiple times (one per vendor binding).  Save rewritten: denominated rows commit entirely to the bound vendor's transaction; non-denominated distribute against per-transaction remaining.  Vendor Reimbursement report no longer attributes phantom payments to vendors who never accepted them.  `single_vendor_mode=True` hides the vendor dropdown on the AdjustmentDialog.  **Charge-integrity guards (Layer 2A/2B/2C):** confirm-time validation that the spinbox value, the engine's capped customer_charged, and the per-vendor reconciliation all agree before any DB write — both PaymentScreen `_confirm_payment` AND AdjustmentDialog `_adjust_transaction` enforce them now (the audit found Adjustments was missing them).  Adjustments also gains the photo validation loop that PaymentScreen had.  **Vendor breakdown UX:** per-vendor "Remaining" column + per-method ✓/✗ eligibility cells on the Payment screen so volunteers see at a glance why a method might not be available.  **Auto-Distribute denomination overage compensation:** non-denom rows now fill the un-overflowed vendors when a denominated method over-allocates one vendor; effective order total carries through to row caps.  **Stale market day guard:** `auto_close_stale_market_days()` runs at startup and auto-closes any Open market day whose date < `eastern_today()`; `create_transaction` refuses to write to a market day with a past date.  Tests pin a stable `eastern_today` in `conftest.py` so historical fixture dates don't trip the guard.  **Error log version preservation:** every `fam_manager.log` line embeds `[vX.Y.Z]` between the level and the logger name; `log_reader` parses it per-entry so an upgrade no longer rewrites old entries to the current version.  **Clear Errors button** (Reports → Error Log): two-stage destructive confirmation, truncates `fam_manager.log` + rotated backups locally AND **device-scoped** clear of the Google Sheets Error Log tab via `delete_rows(market_code, device_id)` — never `ws.clear()` (which would wipe other devices' rows from the shared sheet).  **System payment methods (schema v25):** `is_system` flag + seeded "Unallocated Funds" method.  Adjustments **customer-gone path**: when a positive reconciliation gap, customer-pay delta, or denomination overage would require the customer to physically pay more than they originally did, a popup asks "Can the customer still be charged?" — Yes saves as-entered, No injects an Unallocated Funds line item (and proportionally reduces existing rows' customer_charged for the delta-only case).  Audit log records a dedicated `UNALLOCATED_FUNDS` action with auto-set reason_code.  Reports get a "FAM Absorbed" column + summary card alongside "FAM Match" (different concepts: match is multiplier on customer payment, absorbed is pure FAM funding).  **Adjustments smart cap:** `_update_row_caps` now mirrors PaymentScreen's `_push_row_limits` — per-row remaining = receipt − OTHER rows, match-limit-aware inflation for non-denom, +1 unit denomination forfeit allowance, signal blocking around `setMaximum`.  Wired into `_on_payment_changed` so caps recompute on every value/method edit.  **Denomination forfeit on Adjustments:** popup with "Yes — customer paid the extra / No — log as Unallocated Funds" buttons, proportional reduction of customer_charged + match + method on existing rows when the No path runs.  **Date range filter on Adjustments** (Last Updated semantics): filter targets the most recent audit_log entry for the transaction (or `created_at` fallback) — matches the coordinator's mental model "what I worked on this period".  Three dates per row: Market Date (business context), Created (first entry), Last Updated (filter target).  **Device-tagged customer labels:** format goes from `C-NNN` → `C-NNN-{TAG}` where TAG is auto-derived from SHA1(device_id) by default or coordinator-overridden in Settings (e.g. `LB1` for "Laptop 1").  Multi-laptop deployments at one market never see colliding customer IDs.  Header chip displays the active tag.  Legacy `C-NNN` format preserved when no device_id is captured (tests, very-early startup).  **Schema v27 cleanup migration:** defensive `DROP INDEX IF EXISTS idx_customer_orders_unique_label` for installs that ran an abandoned in-flight v26 build (no `_migrate_v25_to_v26` in the chain — v27 is the canonical successor to v25).  **Market Location dropdown sync:** combo now seeks to the open market_id when a market day is active so the (disabled) selector matches the status header.  **Market Delete:** Settings → Markets gains a Delete button with safety gate — refuses when any `market_days` reference the market, cascades junction-table cleanup when the row is clean.  **2018+ tests across 38+ files.** |
| v1.9.8  | 2026-04-24 | **In-app Help system + FMNP payment-method toggle**.  New Help sidebar item with four tabs: (1) **Walkthrough** — animated 5-stage workflow training ("Your Day at the Market") with custom-painted flat-icon pictograms (18 hand-drawn QPainter icons), looping animation per stage, Next-button pulse after first iteration, manual prev/next/restart/pause/skip-tour controls; (2) **Browse** — 51 curated articles across 8 categories with live keyword search; (3) **Troubleshooting** — 10 symptom-based decision-tree flows; (4) **System Status** — live diagnostic snapshot with Copy Diagnostic Info button.  No AI involvement — all answers are curated text.  PROJECT_INSTRUCTIONS §8a Help Content Discipline rule added: any user-facing change updates the matching help article in the same commit.  **FMNP payment method is now togglable** from Settings → Payment Methods (previously locked) — defaults to inactive on fresh installs / Load Defaults so it does not appear as a payment-row option on the Payment Screen.  The dedicated FMNP Entry screen continues to function independently of this toggle.  All FMNP help content corrected to accurately describe the reimbursement model: vendor matches at 2× face value at the booth, vendor cashes the original check, FAM reimburses face value at end-of-month so the vendor is made whole; FMNP (External) is included in "Total Due to Vendor". 231 new tests (Help library structural integrity, walkthrough widget behavior, looping + flash logic, custom icon library, FMNP toggle independence, Reset-to-Defaults log file clearing, vendors.name UNIQUE migration v22→v23). 1822 tests across 32 files. |
| v1.9.7  | 2026-04-24 | Sync + Drive reliability bundle, sized for the upcoming heavy-FMNP market. **Sync-signal coverage:** (1) FMNP entry deletion now triggers a cloud sync. (2) Payment confirmation fires the sync signal regardless of whether the volunteer returns to Receipt Intake; split `payment_confirmed` (always, drives sync) from new `return_to_intake_requested` (conditional, drives navigation). (3) AdminScreen gained `data_changed` emitted on successful adjustments and voids. (4) ReceiptIntake gained `data_changed` emitted on individual-receipt void, customer-session abandon, and pending-order delete. All new sync triggers ride the existing 60-second cooldown. **AdjustmentDialog parity:** Row charges now capped at receipt total (previously unlimited) and new `⚡ Auto-Distribute` button mirrors Payment Screen — reset non-denominated rows and redistribute respecting denominations/match percents; denominated rows with charges stay locked. **Drive verification correctness fix (the critical one for heavy FMNP):** `_verify_file_in_drive` returned `bool` where `False` conflated "confirmed missing" with "couldn't verify right now", so a transient DNS hiccup during verification would clear every in-flight URL and trigger a mass re-upload on the next sync (the "Drive re-upload storm" bug). Introduced `VerifyResult` tri-state enum (`EXISTS` / `TRASHED_OR_MISSING` / `UNKNOWN`); callers only clear URLs on confirmed `TRASHED_OR_MISSING`; network/auth/5xx errors return `UNKNOWN` and preserve the URL for retry next cycle. Network errors now log a single-line WARN instead of a full traceback. **Verification throttle:** the full URL sweep runs at most once per 10 minutes regardless of how many syncs fire in between — reduces Drive API load 10× at heavy-FMNP markets without affecting new-photo upload responsiveness (uploads still run every sync). 44 new tests total: 20 for sync-signal coverage with source-level integration guards, 24 for Drive tri-state verification + throttle + URL preservation on network error. 1591 tests across 26 files |
| v1.9.6  | 2026-04-24 | Critical hotfix: auto-update downloads failed with `CERTIFICATE_VERIFY_FAILED` on every production laptop because `urllib` in the PyInstaller-frozen build had no trusted CAs — OpenSSL's compiled-in search paths do not resolve inside the bundle, so `ssl.create_default_context()` returned an empty trust store and every HTTPS request failed verification. Fix builds an explicit SSL context from `certifi.where()` (the CA bundle is already packaged via `collect_data_files('certifi')` in the spec) and reuses it across every outbound call in `check_for_update` and `download_update`. Cached so it's only built once per process. Conservative fallback to platform default if certifi is unavailable (dev-mode safety). 7 new tests verifying the context uses certifi, enforces certificate and hostname verification, is cached across calls, and is explicitly passed to every `urlopen`. Regression guard prevents any future caller from relying on the default context. Without this fix, auto-update was entirely non-functional in production. 1547 tests across 24 files |
| v1.9.5  | 2026-04-24 | Hotfix: sync indicator no longer falsely displays green "Online" when the laptop is offline. The prior indicator was reading `last_sync_at` from the database and painting green whenever a past sync had succeeded, which misled volunteers into thinking they were connected at markets with no internet. Integrated Qt `QNetworkInformation` (Windows Network List Manager backend, no outbound probes) so the idle indicator reflects actual OS-reported reachability. Relabeled every state to describe what the app knows: "Last sync OK" / "Sync failed" / "Syncing…" / "Attention" / "No network" / "Not synced yet" — never the ambiguous "Online"/"Offline". Disconnection events now repaint within a second via Qt's `reachabilityChanged` signal. "No network" state includes reassurance text "data safe locally" so volunteers aren't alarmed. 14 new tests (indicator state labels + regression guard that "Online"/"Offline" never appear + OS-network helper + update-visibility state selection), 1540 tests across 24 files |
| v1.9.4  | 2026-04-10 | Auto-update hardening release: (1) updater probes release zip to locate `FAM Manager.exe` and hard-codes the exact source path in the batch script, eliminating silent install failures with double-nested folder structures; (2) `_fam_update.log` in %APPDATA% for post-mortem diagnosis; (3) path-traversal guard rejects unsafe zip member entries; (4) PowerShell path escaping so installs under user paths with apostrophes (e.g. `C:\Users\O'Brien\…`) do not silently fail; (5) pending-update marker file written before batch launches, checked on next startup — version mismatch now surfaces a visible error dialog instead of silent no-op; (6) blocking `pause` statements removed from redirected batch script; 36 new tests including runtime batch execution against synthetic installs, 1518 tests across 24 files |
| v1.9.3  | 2026-04-07 | Hotfix: penny reconciliation added to payment save path (receipt ±1¢ drift eliminated), match limit query includes Adjusted transactions (returning customer cap no longer bypassed after admin edit), 1473 tests across 24 files |
| v1.9.2  | 2026-04-03 | Production readiness release: exhaustive financial audit (all money paths traced UI → DB → reports → ledger → sync), 50 new end-to-end UI integration tests, three-way reconciliation verified across all outputs, production readiness assessment for board review, 1470 tests across 24 files |
| v1.9.1  | 2026-04-02 | Fix: match-cap-aware charge input — daily match limit now correctly raises the charge field maximum so customers can enter the full amount owed when their match is capped; auto-distribute and collect-line-items also cap-aware; 24 new edge case tests (returning customer cumulative cap, void exclusion, penny reconciliation under cap, 200% match with cap, cap=0/1¢ boundaries), 1365 tests across 23 files |
| v1.9.0  | 2026-04-02 | Automated UI test suite (pytest-qt: payment screen, end-to-end workflows, market-day simulation), model-level market day lifecycle guard, max-cap clamping validation, payment method CRUD safety tests, comprehensive documentation lock-in (TECHNICAL_OVERVIEW, USER_GUIDE, PROJECT_INSTRUCTIONS), developer guardrails and known-limitations guide, 1333 tests across 23 files |
| v1.8.6  | 2026-04-01 | Integer-cents financial engine (schema v22), penny reconciliation, FMNP check splitting via integer division, money.py boundary helpers, three-way reconciliation tests (DB/Ledger/Sheets), 1218 tests across 19 files |
| v1.8.5  | 2026-03-12 | Production hardening: Drive API retry with exponential backoff, FMNP dual-source sync (per-check rows with photo links + Transaction IDs), scrollable FMNP photo slots, resizable report columns, auto re-upload of deleted/trashed Drive photos, inherited folder permissions, dead URL hash cache cleanup, QThread lifecycle fix, data collection off UI thread, 3 new DB indexes, schema v21, 1095 tests |
| v1.8.0  | 2026-03-11 | Photo receipts, multi-photo FMNP, Google Drive photo sync, 3-layer SHA-256 dedup, charge-based payment input, agent tracker, denomination validation, schema v18, 1036 tests |
| v1.7.0  | 2026-03-09 | Google Sheets cloud sync, auto-update from GitHub Releases, sync/update packages, 618 tests |
| v1.6.1  | 2026-03-06 | Tutorial auto-configure, market code/device ID, receipt printing, settings import/export, database backups, ledger backup, data dir migration, global exception handler, 479 tests |
| v1.5.1  | 2026-03-04 | First-run tutorial, single-instance prevention, PyInstaller fix |
| v1.5.0  | 2026-03-03 | Interactive tutorial overlay, production-readiness improvements |
| v1.4.1  | 2026-03-02 | Custom FAM logo and window icon |
| v1.4.0  | 2026-03-01 | Reports & charts, ledger backup, real seed data |
| v1.3.0  | 2026-02-28 | FMNP payment integration, UI density optimization |
| v1.2.0  | 2026-02-28 | UI polish — row heights, button styles, chart scaling |
| v1.0    | 2026-02-26 | Initial release |

---

## 12. Cloud Sync (Google Sheets + Drive)

### Architecture
Optional one-way sync from local SQLite to a shared Google Spreadsheet + Google Drive.

| Module | Purpose |
|--------|---------|
| `sync/base.py` | `SyncResult` dataclass |
| `sync/data_collector.py` | Queries DB for summary, vendor, payment, transaction, FMNP data + photo URLs. FMNP entries pull from both fmnp_entries table and payment_line_items (FMNP method), expanding into per-check rows with individual photo links and Transaction IDs |
| `sync/gsheets.py` | Google Sheets backend via `gspread` (service account auth) |
| `sync/drive.py` | Google Drive photo upload via REST API (uses `google-auth` AuthorizedSession) |
| `sync/manager.py` | `SyncManager` — orchestrates data collection + backend calls + agent tracker |
| `sync/worker.py` | `SyncWorker(QObject)` — runs sync in background QThread |

### Credentials
- Service account JSON stored at `{data_dir}/sync_credentials.json`
- Spreadsheet ID in `app_settings` (key: `sync_spreadsheet_id`)
- Drive folder ID in `app_settings` (key: `sync_drive_folder_id`)
- Credentials loaded flag in `app_settings` (key: `sync_credentials_loaded`)

### Photo Upload (Google Drive)
- Photos uploaded to a shared Drive folder ("FAM Market Manager Photos")
- Organized hierarchy: Root > Market Name > Payment Type subfolders
- Two-layer upload dedup: rel_path cache (in-memory) + SHA-256 content hash (DB-persisted)
- Drive URLs written back to `photo_drive_url` columns in FMNP entries and payment line items
- Photo URLs included in synced spreadsheet data for remote visibility
- Files inherit parent folder permissions (no public "anyone with link" sharing)
- Dead URL detection: each sync verifies existing Drive URLs (including trashed files), clears stale URLs + hash cache entries, and re-uploads missing photos automatically
- Retry with exponential backoff for transient Drive API errors (429, 500, 502, 503)

### Agent Tracker
- Each sync appends a row to the "Agent Tracker" sheet with device metadata
- Includes: device_id, market_code, app_version, sync timestamp, sheet counts, sync status
- Keyed by device_id (upsert behavior) — one row per device

---

## 13. Auto-Update (GitHub Releases)

### Architecture
Self-update via GitHub Releases API. Uses `urllib.request` (stdlib — no new dependencies).

| Module | Purpose |
|--------|---------|
| `update/checker.py` | URL parsing, version comparison, GitHub API, download, batch script generation |
| `update/worker.py` | `UpdateCheckWorker` + `UpdateDownloadWorker` (QThread workers) |

### Key functions in `checker.py`
- `parse_github_repo_url(url)` — validates GitHub URL, extracts (owner, repo)
- `compare_versions(current, remote)` — semantic version comparison (-1/0/1)
- `check_for_update(owner, repo, version)` — calls GitHub API, returns release info
- `download_update(url, dest, callback)` — downloads in 64KB chunks with progress
- `verify_download(path, expected_size)` — file size verification
- `generate_update_script(app_dir, zip)` — writes `_fam_update.bat` to AppData

### Update flow
1. App checks GitHub on launch (rate-limited to once/24h) or user clicks "Check for Updates"
2. If update found, user clicks "Download & Install" (blocked while market day open)
3. Download verified against GitHub-reported file size
4. Batch script generated: waits for exe exit → backs up app dir → extracts zip → copies → relaunches
5. Previous version backed up at `{data_dir}/_update_backup/`

### Safety
- Running exe can't replace itself → batch script waits for exit
- Download size verified against GitHub API
- Full backup of previous version before overwrite
- Install blocked while market day is open
- Dev mode: install button disabled with message
- Default repo URL: `DEFAULT_REPO_URL` constant in `app_settings.py`

---

## 14. Money Handling Contract (Integer Cents)

All monetary values throughout the application are stored, computed, and transmitted
as **integer cents** (e.g. `$89.99` = `8999`). This eliminates IEEE 754 float
precision drift that accumulates when adding many dollar values.

### The Rule

| Layer | Representation | Conversion |
|-------|---------------|------------|
| Database (schema v22+) | INTEGER cents | Migration v21→v22 converted all REAL dollar columns |
| Python business logic | `int` cents | All `calculations.py`, models, sync, export |
| UI input (QDoubleSpinBox) | Dollar float | `dollars_to_cents()` on read from widget |
| UI display | Dollar string | `format_dollars(cents)` or `cents_to_dollars(cents)` at display boundary |
| CSV/Ledger export | Dollar float | `cents_to_dollars()` at write boundary |
| Google Sheets sync | Dollar float | `cents_to_dollars()` in `data_collector.py` |

### Boundary Helpers (`fam/utils/money.py`)

```python
dollars_to_cents(dollars: float) -> int    # UI input → internal
cents_to_dollars(cents: int) -> float      # internal → display/export
format_dollars(cents: int) -> str          # internal → "$X.XX"
format_dollars_comma(cents: int) -> str    # internal → "$X,XXX.XX"
```

### Anti-Patterns to Avoid

- **Float accumulation**: Never sum `cents_to_dollars()` results across multiple rows.
  Accumulate in integer cents, convert once at the end.
- **Dollar arithmetic**: Never do `receipt_total / 2` in dollar space. Work in cents.
- **Mixed types**: A variable is either cents (int) or dollars (float), never ambiguous.

### Known Dollar Island

`large_receipt_threshold` in `app_settings` is stored and compared as a dollar value
(float). It is compared only against QDoubleSpinBox dollar values in the UI — it never
enters the cents pipeline. This is intentional and documented, not a bug.

### Penny Reconciliation

When 100% match methods split an odd-cent total, exact halving is impossible.
`calculate_payment_breakdown()` detects a ±1¢ gap between `allocated_total` and
`receipt_total` and absorbs it into the FAM match of the largest matched line item.
The customer charge is never adjusted — only the FAM subsidy absorbs rounding.

---

## 15. Data Integrity & Reconciliation

### Three-Way Invariant

For every completed transaction: **DB == Ledger == Sheets**

| Source | Where | How verified |
|--------|-------|-------------|
| DB | `transactions` + `payment_line_items` tables | Direct SQL queries |
| Ledger | `fam_ledger_backup.txt` | Parsed by `_get_ledger_totals()` in tests |
| Sheets | Google Sheets sync payload | Read from `data_collector.py` output |

### FMNP Treatment Across Layers

FMNP external entries (from the FMNP Entry screen) are tracked in the `fmnp_entries`
table, **separate from** transaction-based `payment_line_items`. Each reporting layer
aggregates them differently by design:

| Layer | Transaction Totals | FMNP External | How Combined |
|-------|-------------------|---------------|-------------|
| **DB queries** | `transactions` + `payment_line_items` | `fmnp_entries` | Separate tables, never mixed |
| **Sync (Market Day Summary)** | receipt_total, customer_paid, fam_match | Not included | Transaction-based only |
| **Sync (FMNP Entries tab)** | Not included | Full FMNP detail | Separate sync sheet |
| **Ledger backup** | Per-transaction lines | FMNP section within each day | Combined in subtotals and grand totals |
| **Reports screen** | FAM Match card | FMNP Match card (separate) | Shown as distinct summary cards |

This is intentional — the Sync Market Day Summary tracks transaction totals only, while
FMNP has its own dedicated Sync tab. The Ledger combines them in grand totals for a
complete financial picture. The Reports screen shows them as separate cards for clarity.

Tests in `test_reconciliation.py` (lines 738-745) explicitly verify this design:
```
sync['receipt_cents'] == db['receipt_cents']  # Sync matches DB (no FMNP)
sync['fmnp_cents'] == db['fmnp_cents']        # FMNP tracked separately
ledger['receipt_cents'] == db['receipt_cents'] + db['fmnp_cents']  # Ledger combines both
```

### Atomic Financial Operations

The four operations that modify confirmed financial data are all atomic
(try/except with rollback on failure):

| Operation | File | Pattern |
|-----------|------|---------|
| `confirm_transaction()` | `transaction.py` | `commit=False` on update + audit, single `conn.commit()`, `rollback()` on error |
| `void_transaction()` | `transaction.py` | `commit=False` on update + audit, single `conn.commit()`, `rollback()` on error |
| `save_payment_line_items()` | `transaction.py` | DELETE + INSERT atomic, `commit=False` support, `rollback()` on error |
| Admin adjustment | `admin_screen.py` | All changes (receipt, vendor, payments, status, audit) in one `try/except/rollback` block |

Non-financial creates (`create_transaction`, `create_customer_order`) commit the record
before writing the audit log. This is acceptable because: (a) creates produce Draft
status records with no financial impact, and (b) the crash window between two commits
is microseconds on a local SQLite file.

### Market Day Lifecycle Rules

| State | Allowed Operations | Guard Level |
|-------|-------------------|-------------|
| **Open** | Create transactions, confirm payments, add FMNP | Full access |
| **Closed** | View reports, adjust/void existing transactions, sync | `create_transaction()` raises ValueError (model-level guard) + Receipt Intake button disabled (UI-level guard) |
| **Reopened** | Same as Open (status returns to 'Open') | Full access restored |

The model-level guard in `create_transaction()` is the authoritative check:
```python
if row['status'] != 'Open':
    raise ValueError(f"Market day {market_day_id} is '{row['status']}' — "
                     f"transactions can only be created on an open market day")
```

### Max-Cap Clamping (Payment Input Limits)

`_push_row_limits()` in `payment_screen.py` runs before every summary update. It:
1. Calculates each row's maximum charge based on remaining balance after other rows
2. Accounts for match percentage (e.g., SNAP 100% match → max charge = remaining / 2)
3. Gives denominated methods +1 unit allowance for forfeit gaps
4. Sets spinbox/stepper maximums with signals blocked to prevent cascade loops

### Snapshot Architecture

`payment_line_items` stores `method_name_snapshot` and `match_percent_snapshot`
at confirmation time. This means:
- Changing a payment method's name or match percentage does NOT alter historical records
- Deactivating a method does NOT delete or modify existing line items
- Reports always reflect the values that were in effect when the payment was confirmed

### What is tested

**`test_reconciliation.py` (25 tests):**
- Single-transaction DB/Ledger/Sheets match (SNAP, Cash)
- Multi-transaction aggregate reconciliation (3-txn, mixed payment)
- 100× $0.33 float-drift stress test
- FMNP entries in totals + uneven check split sums exactly
- Edit receipt total → re-export consistency
- Void transaction exclusion from totals
- Save/reload/edit/save persistence round-trip
- 10 edge-case amounts (parametrized: $0.01, $0.99, $100.00, etc.)
- Per-row detailed ledger vs DB comparison
- Market day summary aggregates vs raw DB
- Per-vendor reimbursement totals vs DB
- Full 3-txn + FMNP session reconciliation
- 50-transaction high-volume reconciliation

**`test_ui_guards.py` (29 tests):**
- Max-cap single row: SNAP, Cash, Food RX, FMNP stepper limits
- Max-cap multi row: cross-row constraint adjustment, three-row scenarios
- Stepper +/- button enable/disable at bounds
- Spinbox clamps typed values exceeding maximum
- Market day guard: closed day raises, reopen re-enables, audit trail
- Receipt intake UI: button disabled when closed, enabled when open
- Adjustment edge cases: multi-method, double-adjust, match cap, audit trail

**`test_payment_method_safety.py` (23 tests):**
- CRUD operations: create, update, soft-deactivate, reactivate
- Market assignment: assign, unassign, idempotent assign, active-only filtering
- Deactivation safety: line items preserved, dropdown hides deactivated, snapshot survives rename/match change
- Reports FMNP separation: FAM Match vs FMNP Match cards show correct split values

### FMNP Check Splitting

When an FMNP entry covers multiple checks, the total is split using integer division
with remainder distribution:

```python
base_check_cents = total_amount_cents // num_checks
remainder_cents = total_amount_cents % num_checks
for i in range(num_checks):
    check_cents = base_check_cents + (1 if i < remainder_cents else 0)
```

This guarantees `sum(all checks) == total` exactly, with no float drift.

---

## 16. Developer Guardrails & Known Limitations

### Guardrails Enforced by Code

| Guardrail | Enforcement Level | Location |
|-----------|------------------|----------|
| Integer cents for all money | DB schema (INTEGER columns) | `schema.py` migration v21→v22 |
| Boundary conversions via money.py | Convention (not compiler-enforced) | `money.py` |
| Closed market day blocks transactions | Model-level ValueError | `transaction.py:create_transaction()` |
| Payment input max-cap | UI-level spinbox/stepper maximum | `payment_screen.py:_push_row_limits()` |
| Atomic confirm/void/save | try/except/rollback | `transaction.py` |
| Snapshot preservation | Separate columns in payment_line_items | `method_name_snapshot`, `match_percent_snapshot` |
| Foreign key integrity | PRAGMA foreign_keys=ON | `connection.py:30` |
| Status validation | Whitelist check | `transaction.py:VALID_TRANSACTION_STATUSES` |
| Soft-delete (no hard deletes) | No DELETE functions for financial records | All models use status fields |

### Guardrails That Depend on Developer Discipline

| Risk | Why It's Not Enforced | Mitigation |
|------|----------------------|------------|
| Using `dollars_to_cents()` at input boundaries | Python has no type distinction between "cents int" and "dollars float" | Naming convention: variables ending in `_cents` are integers |
| Not summing dollar floats across rows | No compiler check | Anti-pattern documented in Section 14 |
| Using `commit=False` for multi-step writes | Easy to forget | Existing critical paths all use it; tests verify atomicity |
| Void → re-confirm prevention | `update_transaction` accepts any valid status | UI does not expose this path; no model guard exists |

### Known Non-Blocking Limitations

1. **QDoubleSpinBox float boundary**: UI input widgets use float dollars internally.
   `dollars_to_cents()` uses `int(round(x * 100))` which handles all 2-decimal-place
   inputs correctly. Replacing with a cents-based QSpinBox would eliminate the
   theoretical concern entirely but is not required for correctness.

2. **Non-atomic audit logging on creates**: `create_transaction()` and similar functions
   commit the main record before writing the audit log entry. The affected records are
   always Draft status (no financial impact). The crash window is microseconds.

3. **No concurrent write tests**: The application is single-user desktop with thread-local
   SQLite connections, WAL mode, and busy_timeout. Concurrent writes are not a realistic
   production scenario.

4. **Settings screen not UI-tested**: Settings is config-only with no financial calculations.
   The underlying model functions (payment method CRUD, market assignment) are fully tested.

---

## 17. Future Milestones

- **Code signing certificate** — Eliminate Windows SmartScreen warning
- **Multi-language support** — Spanish/English toggle for volunteers

---

## 17b. Audit Protocol — UI State-Machine Fuzz (lesson learned after 4th onsite bug)

After the v1.9.10 cycle produced **four onsite bugs in a row** all
in the same family — multi-step UI sequences where lists / indices
/ invariants drift between steps — I added a state-machine fuzz
tier that drives the actual `PaymentScreen` widget through random
sequences of legal volunteer actions.

The bug family being closed:

1. Auto-distribute clamping locked denom rows (multi-vendor overage)
2. Per-vendor 1¢ drift (forfeit math in display vs save)
3. SNAP cap-deficit inflation clamping bound denom rows
4. Adding a row mid-update → IndexError in forfeit Pass 3

All four required *sequences* of actions to manifest.  Static
single-state cross-layer tests (V1–V5) couldn't have caught them.

### The new tier

`tests/test_ui_state_machine_fuzz.py` — 35 seeds × 30-100 actions.
Each action is a legal volunteer move drawn from a weighted pool:

| Action | Weight | What it does |
|--------|--------|--------------|
| `add_row` | 25 | Add a payment row with random method + maybe a charge |
| `set_charge` | 20 | Type a random charge on a random row |
| `bump_charge` | 15 | Slightly adjust a charge (small mutation) |
| `change_method` | 10 | Pick a different method on an existing row |
| `change_vendor` | 8 | Re-bind a denom row to a different vendor |
| `delete_row` | 8 | Remove a random row entirely |
| `auto_distribute` | 7 | Click ⚡ Auto-Distribute |
| `zero_charge` | 7 | Clear a row's charge to $0 |

After **every** action: `_recompute()` on each row, `_update_summary()`
on the screen, then validate V1, V3, V5 + engine `is_valid` +
no-crash.  Any failure dumps the full action log for deterministic
replay.

Plus `tests/test_realistic_complex_flows.py` — 4 explicit
human-like scenarios that cover patterns the fuzzer might miss
by chance:

* **Mistake-prone volunteer**: adds wrong row, deletes, re-adds,
  changes method, fixes vendor binding, types wrong amount, undoes.
* **Save-as-draft + resume**: partial entry → save draft → reload
  order → state preserved.
* **Multi-iteration adjustment chain**: confirm → adjust receipt →
  adjust methods → adjust receipt down → void.  I1+I2 verified
  after every adjustment.
* **Returning customer + cap straddling + void recovery**: visit 1
  uses match, visit 2 hits cap, void visit 1, cap restored.

Both files are part of the standing release gate (gate 1 pytest).

### Instruction template — for any future audit request

When asking for audits going forward, prefer this phrasing:

> *"Run the state-machine fuzzer and the realistic-complex-flow
> tests as part of the audit.  Any new bug class found during a
> manual screenshot test should immediately be added as both a
> regression test AND a new action type / scenario in the fuzzer.
> Audit the screen, not the schema — and audit transitions, not
> just static states."*

That phrasing forces:
1. UI-level testing (not just data-layer)
2. Multi-step sequences (not just single snapshots)
3. New bug classes get pinned by both deterministic AND randomized
   tests

### When you find a new bug

1. Reproduce it in a deterministic test (`test_<name>.py`).
2. Add a corresponding action / scenario to the fuzzer or the
   complex-flow file so the *class* of bug is caught next time
   even if the specific instance differs.
3. Fix the bug; both tests pass.
4. Re-run the release gate.

This is how we close the gap on "first-test-finds-a-bug" patterns.

---

## 17a. Audit Protocol — UI-Visible Field Invariants (lesson learned 2026-04-30)

The 2026-04 onsite produced **two consecutive bugs** that data-layer
audits did not catch but were visible in the PaymentScreen the moment
a human looked at it:

1. Auto-Distribute silently clamped a bound denom row from 14 → 13
   units (multi-vendor multi-overage interaction in
   ``_push_row_limits``).
2. Per-vendor "Remaining" column showed ``$0.01`` on Juice Bar and
   ``-$0.01`` on Elfinwild despite the order totaling correctly
   (denomination forfeit using order-level overage instead of
   per-vendor overage — and a *second* copy of the same bug in the
   display path that re-introduced the drift after the save-path fix).

Both bugs lived in the **derivation layer** between the engine output
and the visible UI cells — a layer prior audits implicitly trusted
because the data layer reconciled.  That trust was misplaced.

### The new protocol

When auditing this codebase, **treat every UI-visible field as part
of the financial contract**.  Specifically:

* For every state-changing action in the UI (entering a charge,
  toggling a method, clicking Auto-Distribute, confirming, voiding,
  adjusting), the following must hold *as displayed on the screen*:

  | ID | Visible-field invariant |
  |----|--------------------------|
  | **V1** | Vendor breakdown table — column "Remaining" is exactly $0.00 for every vendor whose receipt is fully allocated (not ±1¢) |
  | **V2** | Σ (per-method cells in a vendor's breakdown row) == receipt − remaining, exactly |
  | **V3** | Summary cards (Total Allocated / Customer Pays / FAM Match) match the sum of corresponding values across rows |
  | **V4** | ``denom_overage_warning`` text reports the exact per-vendor forfeit that the save will commit |
  | **V5** | Per-row visible Total == visible Charge + visible Match |

* Tests for these invariants live in
  ``tests/test_ui_visible_field_invariants.py``.  When the engine
  output, save path, and visible display *all three* must agree —
  not just two of three.

* When fixing a bug in any forfeit / penny-reconciliation /
  per-vendor distribution path, **search for parallel implementations
  of the same logic in the display path**.  ``_apply_denomination_
  forfeit`` is now the single source of truth — both ``_update_
  summary`` (display) and ``_confirm_payment`` (save) call it.  A
  prior split-implementation pattern is what allowed the second bug
  to re-introduce the drift the user found.

### Instruction template for future audit requests

When asking for an audit, prefer this phrasing:

> "Treat every UI-visible field as part of the financial contract.
> After every state-changing action, snapshot every cell of the
> vendor breakdown table, every summary card, and every warning
> label.  Assert each derived value matches what the engine and the
> save path would produce, to ±0¢."

This forces the audit into the layer that's been the actual blind
spot — not just data-layer math.

---

## 17c. Phase 6 Engine Consolidation (v1.9.10) — DO NOT DUPLICATE

> **Single source of truth:**
> ``fam.utils.calculations.resolve_payment_state(receipt_total,
>   items, match_limit, apply_denomination_forfeit_fn)``.
>
> **Anyone adding a new screen, dialog, or save path that touches
> cap-aware payment math MUST call this function.**  Do not
> re-implement the cap fallback, the forfeit reduction, or the
> Pass 4 give-back locally.  18 onsite-reported bugs came from
> drift between parallel implementations of this exact math.

### What lives where

| Concern | Location | Notes |
|---|---|---|
| Per-row math (`charge ↔ method_amount`) | `calculations.py::charge_to_method_amount`, `method_amount_to_charge` | Stable contract |
| Engine cap math (proportional / fallback) | `calculations.py::calculate_payment_breakdown` | Lower-level; usually call `resolve_payment_state` instead |
| Smart auto-distribute | `calculations.py::smart_auto_distribute` | Engine-level row seeding |
| **Canonical engine resolution** | **`calculations.py::resolve_payment_state`** | **Always start here** |
| Per-vendor-aware forfeit | `payment_screen.py::_apply_denomination_forfeit` | Passed as `apply_denomination_forfeit_fn` to `resolve_payment_state`.  Knows about vendor bindings; doesn't belong in the pure engine |
| Per-vendor distribution at save | `payment_screen.py::_distribute_and_save_payments` | Phase 1 (denom→bound vendor) + Phase 2 (non-denom proportional split).  After Phase 6, this consumes already-cap-resolved items — its own cap step is a no-op when items came from `resolve_payment_state` |
| `_collect_line_items` (PaymentScreen) | `payment_screen.py` | Builds items + applies receipt-cap on non-denom method.  Always followed by `_resolve_engine_state` (which calls `resolve_payment_state`) before save |
| `get_new_line_items` (AdjustmentDialog) | `admin_screen.py` | Builds items + receipt-cap + delegates to `resolve_payment_state` |

### Why AdjustmentDialog is NOT unified with PaymentScreen

Pre-launch (v1.9.10) the dialog edits one transaction at a time.
Unifying it with PaymentScreen would be a multi-day refactor.
Instead, **parity** is enforced by
`tests/test_adjustment_payment_parity_matrix.py` — every single-
vendor scenario in the cross-layer matrix runs through BOTH
screens and the outputs must agree to the cent.  If you change
PaymentScreen's payment math, this parity test will fail until
the equivalent change is made in AdjustmentDialog (or vice
versa).  Re-unification is on the v1.10 roadmap.

### Editing the canonical engine

When changing `resolve_payment_state`:

1. Update or add equivalence tests in
   `tests/test_resolve_payment_state_equivalence.py`.
2. Run the full cross-layer matrix
   (`tests/test_cross_layer_parity_matrix.py`) — this is your
   widest safety net.
3. Run the AdjustmentDialog parity matrix
   (`tests/test_adjustment_payment_parity_matrix.py`).
4. Run the engine ↔ save-path equivalence test
   (`tests/test_engine_save_path_equivalence.py`).
5. Run the full release audit gate
   (`scripts\run_release_audit.bat`).

Steps 2-4 each take <30s individually; step 5 takes ~2-3
minutes.  Don't merge a `resolve_payment_state` change without
all of them green.

### Known fuzz finding deferred to v1.10

`test_ui_driven_fuzz.py` seed 2 surfaces a 1-unit phantom in
``PaymentRow`` when the user rapidly swaps a row's method between
non-denom (Cash) and denom (FB).  Engine consolidation does NOT
fix this — root cause is in PaymentRow widget state management.
Tracked for v1.10 PaymentRow refactor.  Real-world impact very
low (manual workflow doesn't trigger the swap-then-immediately-
read pattern the fuzzer exercises).

---

## 18. Release Audit Procedure (mandatory gate)

**Every release** of FAM Market Manager — including hotfixes,
documentation-only changes, and "trivial" fixes — must pass the
full Production Readiness Audit before being tagged, built, or
distributed.  This is non-negotiable.  The procedure was hardened
in v1.9.9 and is documented in full at
`docs/RELEASE_AUDIT_PROCEDURE.md`.

### The three gates

| # | Gate | Command | Time |
|---|------|---------|------|
| 1 | Full pytest suite | `python -m pytest` | ~65 s |
| 2 | Production simulation | `python -m scripts.production_sim` | ~2 s |
| 3 | v1.9.9 stress simulation | `python -m scripts.v1_9_9_stress_sim` | ~1 s |

### One-command runner

```bat
scripts\run_release_audit.bat
```

Halts on the first gate that fails.  Exit code 0 only when all
three are clean.

### What each gate proves

* **Gate 1 (pytest)** — every documented behavior still holds:
  formula correctness, schema migrations, UI guards, sync,
  auto-update, photo dedup, audit-log coverage, export
  reconciliation, and the 15 production stress scenarios added in
  v1.9.9.
* **Gate 2 (production sim)** — 43 reconciliation invariants
  across small/medium/heavy market days (300+ simulated
  transactions): receipt totals = method allocations, vendor
  reimbursement reconciles, FAM Match report = DB sum, no
  dangling Drafts, DB triggers reject negative amounts, abrupt
  shutdown does not lose Drafts.
* **Gate 3 (v1.9.9 stress sim)** — 34 strict reconciliation
  invariants in the "zero tolerance" mega-scenario: 10-vendor
  single-customer order reconciles across 4 report surfaces,
  returning customer cap correctly accumulated and freed by void,
  5-iteration adjustment chain preserves invariant after every
  step, $0.01 / 200% / 0% / `match_limit=0` / multi-denom edge
  cases all pass.

### Acceptable warnings

`[WARN]` lines that correspond to documented gaps already pinned
in `tests/test_audit_coverage_gaps.py` (vendor / payment-method /
settings CRUD missing audit-log entries) are **acceptable**.  Any
**new** warning is a release blocker until investigated.

### When a gate fails

1. **Stop.**  Do not tag, build, or distribute.
2. Reproduce with `pytest -x -v <failing_test>` or by re-running
   the simulation that flagged it.
3. Fix the **underlying code**, not the test.
4. Re-run **all three gates** — fixes in one area sometimes break
   another.
5. Add a regression test that would have caught the failure.
6. Commit fix + test together.  Reference the gate in the commit
   message (e.g. `regression caught by v1_9_9_stress_sim Phase 5`).

### When you add new behavior worth pinning

| Type of change | Add a test in |
|----------------|---------------|
| Per-row math / reconciliation | `tests/test_match_formula.py`, `tests/test_reconciliation.py`, `tests/test_production_stress.py` |
| New audit-log surface | move from "gap" to "logged" class in `tests/test_audit_coverage_gaps.py` |
| New CSV export | add a CSV-vs-DB test in `tests/test_export_reconciliation.py` |
| New edge case found in operations | add a Phase to `scripts/v1_9_9_stress_sim.py` |
| New release-gate invariant | add a numbered entry to `docs/RELEASE_AUDIT_PROCEDURE.md` §3 |

### The reconciliation contract

For every confirmed/adjusted transaction `T`:

```
T.receipt_total
  = Σ T.payment_line_items.method_amount
  = Σ T.payment_line_items.customer_charged
  + Σ T.payment_line_items.match_amount
```

For every market day `D`, every report surface `R`, every CSV
export `E`:

```
Σ T.receipt_total over D
  = Vendor Reimbursement total (UI report)  for D
  = Vendor Reimbursement CSV grand total    for D
  = FAM Match "Total Allocated"             for D
  = Detailed Ledger non-voided receipt sum  for D
```

**These equalities must hold to ±0¢.**  A penny of drift in any
one reconciliation is a financial-integrity regression and a
release blocker — not a rounding curiosity.

---

## §18 Rewards program (v1.9.10+, schema v30) — informational, NOT financial

The Rewards add-on is a customer-facing marketing/loyalty layer
(default rule: SNAP × $5 → $2 × JH Food Bucks).  It runs entirely
**outside** the financial pipeline.  Future contributors must
preserve the carve-out:

* **Schema v29** added `reward_rules` (config-only).
* **Schema v30** added `generated_rewards` (write-once snapshot
  history of rewards handed out at confirmation time).
* Neither table touches `transactions`, `payment_line_items`, or
  any existing constraint/trigger.
* **Reward amounts are stored as a write-once snapshot** in
  `generated_rewards`.  The row is inserted atomically with the
  payment commit and **NEVER modified after** — this is the
  receipt-of-record posture (matches what the cashier handed the
  customer, immune to later rule edits / voids / adjustments).
* The Reports tab and cloud-sync collector READ stored rows; they
  do not recompute anything.

**What you must NOT do** (enforced by
`tests/test_generated_rewards_report.py::TestNoFinancialPipelineImpact`
and `tests/test_generated_rewards_persistence.py`):

* Add a `reward_amount` / `reward_method_id` / `reward_units` /
  `rewards` column to `payment_line_items` or `transactions`.
* Mix reward totals into vendor reimbursement, FAM match, or any
  per-line invariant.
* Make rewards data load-bearing for any financial reconciliation
  test.
* **UPDATE or DELETE rows in `generated_rewards` based on void /
  adjust / rule-edit events.**  The table is write-once.  The
  only legitimate writer is `record_generated_rewards` from
  `fam/models/generated_reward.py`, called atomically with the
  initial payment commit.
* Recompute rewards from current transaction state in any read
  path.  Reports + cloud sync read stored rows directly.

Where rewards surface:

1. Payment confirmation dialog — `_build_rewards_zone` in
   `fam/ui/widgets/payment_confirmation_dialog.py`.  Pre-commit
   computation only (no DB row exists yet).
2. **Persistent write at confirmation** — `record_generated_rewards`
   in `fam/models/generated_reward.py`, called from
   `_confirm_payment` inside the same DB transaction as the
   payment commit.  Idempotent (skips if rows already exist for
   the order).
3. Printed receipt — `_format_receipt_html` in
   `fam/ui/payment_screen.py`.  Reads stored rows via
   `get_generated_rewards_for_order`.
4. Reports screen — `_load_generated_rewards` tab in
   `fam/ui/reports_screen.py`.  Reads stored rows.
5. Cloud sync — `_collect_generated_rewards` in
   `fam/sync/data_collector.py`, registered in
   `REQUIRED_SYNC_TABS` so it uploads by default.  Reads stored
   rows.

Default seed data inserts the SNAP × $5 → $2 × FB rule
**active**.  Coordinators disable via Settings → Rewards if their
market does not run a loyalty program.

Full documentation: `docs/FINANCIAL_FORMULA.md § 11` and
`docs/USER_GUIDE.md § Rewards Program`.
