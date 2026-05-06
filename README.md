# FAM Market Manager

A desktop application for managing Food Assistance Match (FAM) market day transactions. Built for volunteers to record customer purchases, track payment methods, calculate FAM matching funds, and generate end-of-day reports.

## Documentation

| Document | For who | When |
|---|---|---|
| **[USER_GUIDE.md](docs/USER_GUIDE.md)** | Anyone learning the app | Long-form manual covering every screen and feature |
| **[EMERGENCY_RUNBOOK.md](docs/EMERGENCY_RUNBOOK.md)** | Volunteers at the booth | **Print this.** Symptom-indexed recovery steps for market-day disasters — designed for no-internet, no-coordinator scenarios |
| **[COORDINATOR_HANDBOOK.md](docs/COORDINATOR_HANDBOOK.md)** | Coordinators / ops leads | Setup, deployment, monthly reconciliation, escalation guidance |
| **[QUICK_REFERENCE.md](docs/QUICK_REFERENCE.md)** | Volunteers at the booth | Single-page cheat sheet — print and tape behind the booth |
| **In-app Help** (sidebar in the app) | Everyone, anytime | 70+ articles, 20 troubleshooting flows, live system-status diagnostic |

## Features

- **Market Day Management** — Open/close market days and track volunteer shifts
- **Receipt Intake** — Record customer purchases with vendor, amount, and payment method
- **Customer Orders** — Group multiple receipts per customer visit with automatic FAM match calculation
- **Payment Matching** — Configurable match percentages per payment method (SNAP, FMNP, Food RX, etc.)
- **Daily Match Limits** — Per-market caps on matching funds per customer
- **Receipt Printing** — Print customer receipts after payment confirmation
- **Reports & Charts** — Revenue breakdowns, vendor summaries, and payment method analytics
- **Admin Adjustments** — Edit, adjust, or void transactions with full audit logging
- **FMNP Check Tracking** — Record and manage Farmers Market Nutrition Program entries with multi-photo support
- **Photo Receipt Capture** — Attach check/receipt photos to FMNP entries and payment transactions with denominated photo slots
- **3-Layer Photo Deduplication** — SHA-256 content hashing prevents duplicate photo attachments within entries (hard block), across transactions (warning), and during Drive upload (silent reuse)
- **Google Drive Photo Sync** — Uploaded check photos sync to organized Google Drive folders alongside spreadsheet data
- **Agent Tracker** — Per-device sync reporting with app version, market code, and last-sync metadata in Google Sheets
- **Settings Import/Export** — Share market configurations across devices via `.fam` files
- **Multi-Market Identity** — Auto-derived market codes and device IDs in transaction IDs, exports, and filenames
- **Automatic Backups** — Periodic database backups with 20-file retention, plus human-readable ledger backup
- **Data Export** — CSV exports with market code and device ID columns for finance team consolidation
- **First-Run Tutorial** — Interactive guided walkthrough with one-click auto-configure option
- **Cloud Sync** — Optional one-way sync of end-of-day reports to Google Sheets for remote viewing
- **Auto-Update** — Check GitHub Releases for new versions, download and install updates with one click

## Installation

1. Download the latest `FAM_Manager_vX.X.X.zip` from [Releases](https://github.com/seansaball/fam-market-manager/releases)
2. Extract the zip to any folder
3. Double-click **FAM Manager.exe** to run
4. Follow the tutorial — on the final step, click **"Yes — Load Default Data"** to auto-configure 3 markets, 23 vendors, and 6 payment methods

No Python installation required. Works on Windows 10/11 (64-bit).

> **Note:** Windows SmartScreen may prompt on first run. Click "More info" then "Run anyway."

## Upgrading

Your data is stored separately in `%APPDATA%\FAM Market Manager\`, so upgrading is safe and simple:

**Option A — In-App Auto-Update (recommended):**
1. Go to **Settings → Updates**
2. Click **"Check for Updates"**
3. If available, click **"Download & Install"** — the app restarts with the new version

**Option B — Manual:**
1. Download the new zip from [Releases](https://github.com/seansaball/fam-market-manager/releases)
2. Replace the old application folder with the new one (or extract to a new location)
3. Launch — the app finds your existing data automatically

No data migration or manual file copying required.

## Development Setup

```bash
git clone https://github.com/seansaball/fam-market-manager.git
cd fam-market-manager

python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt

python run.py
```

## Running Tests

```bash
python -m pytest tests/ -v
```

**3,387 tests across 60+ test files** covering: formula validation, match limits, returning customers, transaction adjustments, FMNP reports, market codes, device IDs, backups, schema migrations, settings import/export, cloud sync (including the multi-workstation invariant — every settings mutation triggers a sync, every cleanup gates by device_id), auto-update, charge conversion, denomination validation, photo storage (with atomic-write hardening), multi-photo workflows, photo deduplication and void/edit cache cleanup, FMNP sync integration, integer-cents boundary validation, three-way reconciliation (DB/Ledger/Sheets), automated UI testing (payment screen, workflows, market-day simulation), max-cap clamping, market day lifecycle guards, market-rename code-shift protection, adjustment edge cases, payment method CRUD safety, match-cap-aware charge input, end-to-end production readiness, v1.9.9 stress scenarios, audit-log coverage, and CSV-vs-DB export reconciliation.

The v1.9.10 hardening pass added: **concurrency baseline** (WAL/FK/busy_timeout pragmas, snapshot isolation, cross-thread visibility), **single-instance lock** (cross-platform with cross-process subprocess test), **atomic I/O & races** (photo writes, ledger writes, concurrent UPDATE), **crash recovery** (mid-transaction kill, migration .bak, idempotent migrations, full backup-restore lifecycle), **input validation** (52 tests against SQL injection, path traversal, unicode round-trip, CSV formula injection, extreme values), **UI volume** (200 vendors, 50 payment methods, 500-customer dropdown, 30-row engine, 50-confirm memory bound), **timezone correctness** (DST spring forward / fall back, year-end, leap day), **offline-quiet logging** (Bethel Park reproducer + classifier coverage), and **auto-update post-hardening** (instance lock + pending-update marker coexistence, atomic-write helper isolation).

## Release Audit Gate (mandatory before every release)

Every release — including hotfixes — must pass the full Production
Readiness Audit before being tagged or distributed:

```bash
scripts\run_release_audit.bat
```

This runs three gates in sequence and halts on the first failure:

1. **Full pytest suite** (~140 s, 3,387+ tests)
2. **Production simulation** — `python -m scripts.production_sim` (43 reconciliation invariants over 300+ simulated transactions)
3. **v1.9.9 stress simulation** — `python -m scripts.v1_9_9_stress_sim` (34 invariants in a 10-vendor mega order, returning customer, adjustment chain, void integrity, edge cases)

Procedure documented at `docs/RELEASE_AUDIT_PROCEDURE.md`.  Inaugural
audit report: `docs/PRODUCTION_READINESS_v1.9.9.md`.  Financial formula
reference: `docs/FINANCIAL_FORMULA.md`.

## Building the Executable

```bash
build.bat
```

Output: `dist\FAM Manager\FAM Manager.exe`

## Tech Stack

- **Python 3.12** + **PySide6** (Qt6)
- **SQLite** (WAL mode) for local data storage
- **Matplotlib** for charts
- **Pandas** for CSV export
- **Folium + pgeocode** for geolocation heat maps
- **gspread + google-auth** for Google Sheets cloud sync
- **PyInstaller** for standalone packaging

## Version History

| Version | Summary |
|---------|---------|
| **v2.0.6** | **Production season release (May 2026).**  Headline additions are **per-vendor payment-method eligibility** (Settings → Vendors), a **configurable rewards engine** (Settings → Rewards), and a redesigned **Payment Confirmation Dialog** with marching-ants action zones and per-method "give / take" rows.  Schema v33 → **v34** through 12 forward migrations from v22 (v1.9.8 baseline), all additive; pre-migration `.bak` is fatal-on-failure.  **Multi-workstation cloud sync invariant systematically guarded** — every settings mutation (vendor / market / payment-method add/edit/toggle/delete, all four assignment dialogs, reward-rule changes) now triggers a full-scope sync; closed market-day mutations (FMNP entries, Admin adjustments / voids) scope correctly via `scope_md_id_override`; Vendor Reimbursement cleanup gates by `device_id` only so non-primary-market rows don't get stranded; reset preserves other devices' rows on the shared sheet by triggering an immediate device-scoped cleanup.  **Market renames protected** — code-changing renames blocked once history exists, code-stable renames always allowed.  **Photo dedup cache cleaned on void / delete / replace** for both transactions and FMNP entries; `update_fmnp_entry` now clears `photo_drive_url` on photo_path change so the upload pipeline re-evaluates.  **Diagnostics polish** — Error Log tab auto-refreshes on every selection; DB fragmentation hint is size-aware (>1000 pages + >30%); device-identity dialog provides PowerShell remediation for image-cloned-laptop MachineGuid issues.  Reports add a **Zip Code column** (Detailed Ledger / Transaction Log / Generated Rewards / FMNP Entries).  **+89 new tests today**, **3,387 tests across 60+ files**, all release gates green. |
| **v2.0.1** | **Pre-deployment hardening pass (2026-05-01).**  Closes every claim-vs-reality gap surfaced by the comprehensive senior-engineer / QA / financial-integrity audit.  All fixes are surgical and additive — none touch the payment engine math.  Schema v32 → v33.  **CRITICAL fixes:** **(1) `InstanceLock` actually wired into `app.py`** — replaces the per-machine kernel mutex with the per-data-folder file lock the v2.0 release notes already claimed.  Two laptops pointing at the same shared `%APPDATA%` can no longer both launch.  **(2) Narrow-scope auto-sync no longer deletes historical rows** on the shared sheet.  Auto-syncs (one-market-day scope) skip stale-row pruning; only manual full syncs delete.  Vendor Reimbursement always runs against ALL market days regardless of scope so per-vendor totals don't oscillate.  **(3) FMNP via Payment Screen now reports the physical face value** (`customer_charged`) instead of `method_amount` — eliminates the 2× over-claim on the FMNP Entries report at 100% match.  **(4) FMNP summary tile** on Reports now sums BOTH paths (FMNP Entry screen + Payment Screen).  **(5) Generated Rewards banner reworded** to match the write-once schema guarantee.  **(6) `void_customer_order`** now emits per-transaction VOID audit rows (matches `void_transaction`).  **(7) Pending-update check moved before `window.show()`** — user is told about a failed update before they can interact.  **HIGH fixes:** open-market-day check before update install no longer silently swallowed; `collect_sync_data` and Reports `_generate_reports` wrapped in single SQLite read transactions for snapshot isolation; pre-migration .bak uses `sqlite3.Connection.backup()` (WAL-aware) with version-stamped filenames + 5-deep retention; `_update_summary` recursion guard; AdjustmentDialog re-fetches transaction status on accept (refuses to save if voided in another window); Receipt-Intake remove-receipt voids parent customer_order when no siblings remain; `.fam` export round-trip preserves `is_active` and `photo_required` (compliance regression closed); `_safe_count` distinguishes operational errors from runtime contention; pending-update marker writes atomically; Reset adds typed-RESET confirmation + pre-reset `.bak` snapshot; defensive `chk_pli_uf_zero_*` triggers enforce Unallocated Funds rows have customer_charged=0 and match_amount=0; orphan `local_photo_hashes` rows cleaned up alongside file deletion.  All 4 release gates remain green; full test suite passing |
| **v2.0.0** | **First production release (2026-05-01).**  The major-version bump from v1.9.9 marks the culmination of a multi-session hardening + documentation pass that brought the app from "functional and battle-tested" to "self-service-grade for non-technical operators with no engineering on call."  **(1) 5-session hardening** — concurrency baseline (WAL/FK/busy_timeout, snapshot isolation, cross-thread visibility), crash recovery (mid-transaction kill, migration `.bak`, full backup-restore lifecycle), input validation (52 tests against SQL injection, path traversal, unicode round-trip, CSV formula injection, extreme values), UI volume (200 vendors, 50 payment methods, 500-customer dropdown, 30-row engine, 50-confirm memory bound), timezone correctness (Eastern across the board, DST + leap-day pinned).  **(2) Cross-platform single-instance lock** (`fam/database/instance_lock.py`) — advisory file lock at `%APPDATA%\FAM Market Manager\.fam_instance.lock` prevents two copies running against the same data folder; fixes a Windows `'a+'` mode byte-range race.  **(3) Atomic photo writes** — `fam/utils/photo_storage.py` now uses tempfile + `os.replace`, so a mid-write crash leaves no half-written JPEGs.  **(4) Offline-quiet sync logging** — DNS / connection errors collapse to ONE warning per sync cycle instead of full tracebacks per sheet tab; ~30× log-noise reduction during outages (Bethel Park 2026-05-01 incident).  **(5) Customer Rewards** add-on — Settings → Rewards configures rules, Payment confirmation dialog shows a GIVE TO CUSTOMER zone, Generated Rewards report tab is a write-once historical record.  **(6) Documentation overhaul for the no-onsite-support scenario** — new `EMERGENCY_RUNBOOK.md`, `COORDINATOR_HANDBOOK.md`, `QUICK_REFERENCE.md`; 11 new in-app help articles (Rewards × 3, instance lock, pending update, glossary, multi-laptop, offline runbook, data-not-on-sheet, restore-from-backup, credentials rotation, end-of-day handoff); 6 new troubleshooting flows; System Status now includes log tail, instance-lock state, pending-update state, and rewards summary.  **(7) Tutorial overlay correction** — Drive folder URL field reference removed (Drive auto-configures from Sheets credentials).  **+277 new tests since v1.9.9**; **3 116 tests across 40+ files**; all 4 release gates green |
| v1.9.9 | **Large 2026-04 onsite-findings bundle.**  Schema v23 → v27.  Highlights: **(1) Per-vendor binding** (schema v24) — `vendor_payment_methods` junction, denominated rows bind to a single vendor via inline dropdown.  **(2) Charge integrity** — Layer 2A/2B/2C confirm-time guards + photo validation + spinbox write-back enforced on BOTH PaymentScreen AND AdjustmentDialog.  **(3) Adjustments customer-gone path + Unallocated Funds** (schema v25) — when an adjustment requires the customer to physically pay more, a popup asks "Can the customer still be charged?"; No injects an Unallocated Funds line item visible in reports as a separate "FAM Absorbed" column.  **(4) Adjustments smart cap + denomination forfeit** matching the Payment screen.  **(5) Adjustments date filter** — targets `last_updated` (most recent audit activity), with new "Last Updated" column alongside "Market Date" and "Created".  **(6) Device-tagged customer labels** — `C-NNN-{TAG}` format prevents collisions across multi-laptop deployments at one market; auto-derived from MachineGuid or coordinator-overridden in Settings.  **(7) Stale market day guard** — auto-closes Open days with past dates at startup.  **(8) Error log version preservation + device-scoped Clear Errors button** — log lines carry `[vX.Y.Z]`; Clear Errors only removes this device's rows from the shared Sheet.  **(9) Market Delete** with safety gate + **Market Location dropdown sync** + **vendor breakdown ✓/✗ eligibility** + **Auto-Distribute denom overage compensation**.  Schema v27 cleanup migration drops a UNIQUE INDEX from an abandoned in-flight v26 build.  ~545 new tests; **2018 tests across 38+ files** |
| v1.9.8 | In-app **Help** system: animated 5-stage walkthrough with custom flat-icon pictograms (looping animation, Next-button pulse, no auto-advance), 51 searchable articles across 8 categories, 10 troubleshooting flows, System Status diagnostic tab. **FMNP payment method now togglable** — default inactive on fresh installs; Settings → Payment Methods toggles availability for the Payment Screen without affecting the FMNP Entry screen. All FMNP help content corrected to describe the reimbursement model accurately (vendor matches at booth at 2× face value, FAM reimburses face value, FMNP-External is in Total Due to Vendor). New §8a Help Content Discipline rule. 231 new tests; 1822 tests across 32 files |
| v1.9.7 | Sync + Drive reliability bundle: (1) Sync-signal coverage — FMNP delete, payment confirm (regardless of nav choice), admin adjust/void, receipt-intake voids now all trigger sync (60-second cooldown applies). (2) AdjustmentDialog caps row charges at receipt total and adds `⚡ Auto-Distribute` button. (3) Critical Drive fix — `_verify_file_in_drive` now returns tri-state (`EXISTS` / `TRASHED_OR_MISSING` / `UNKNOWN`); network/auth errors no longer trigger spurious URL clearing and re-upload storms. (4) Verification throttled to once per 10 minutes — 10× Drive API load reduction at heavy-FMNP markets without slowing new-photo upload. 44 new tests; 1591 tests across 26 files |
| v1.9.6 | Critical hotfix: auto-update downloads failed with `CERTIFICATE_VERIFY_FAILED` in frozen builds. `urllib` had no trusted CAs because OpenSSL's default search paths don't exist inside a PyInstaller bundle. Fix builds an explicit SSL context from `certifi.where()` and passes it to every outbound HTTPS call. 7 new tests; 1547 tests across 24 files |
| v1.9.5 | Hotfix: sync indicator no longer shows false green "Online" when the laptop is disconnected. Now uses Qt `QNetworkInformation` for OS-level reachability and relabels all indicator states to describe what the app actually knows ("Last sync OK", "Sync failed", "No network", "Not synced yet"). Disconnection repaints within a second. 14 new tests, 1540 tests across 24 files |
| v1.9.4 | Auto-update hardening: nested-zip bug fixed via zip probe + hard-coded source path, update log file (`_fam_update.log`) for diagnosis, path-traversal guard on zip entries, PowerShell escaping for paths with apostrophes, pending-update marker so silent install failures surface as a visible error dialog on next launch, 36 new tests including runtime batch execution, 1518 tests across 24 files |
| v1.9.3 | Hotfix: penny reconciliation in payment save path, match limit includes Adjusted transactions, 1473 tests across 24 files |
| v1.9.2 | Production readiness release: exhaustive financial audit, 50 new end-to-end UI integration tests, three-way reconciliation verified, production readiness assessment for board review, 1470 tests across 24 files |
| v1.9.1 | Fix: match-cap-aware charge input — daily match limit now correctly raises charge field max when match is capped; auto-distribute and collect-line-items also cap-aware; 24 new edge case tests, 1365 tests across 23 files |
| v1.9.0 | Automated UI test suite (pytest-qt), model-level market day lifecycle guard, max-cap clamping validation, payment method CRUD safety tests, comprehensive documentation lock-in, developer guardrails and known-limitations guide, 1333 tests across 23 files |
| v1.8.6 | Integer-cents financial engine: all monetary storage/computation in integer cents (schema v22), penny reconciliation, FMNP check splitting via integer division, three-way reconciliation tests (DB/Ledger/Sheets), 1218 tests across 19 files |
| v1.8.5 | Production hardening: Drive retry logic, FMNP dual-source sync (per-check rows), scrollable photo slots, resizable report columns, auto re-upload of deleted Drive photos, inherited folder permissions, 3 new DB indexes, 1095 tests |
| v1.8.0 | Photo receipts, multi-photo FMNP, Google Drive photo sync, 3-layer SHA-256 dedup, charge-based payment input, agent tracker, denomination validation, 1036 tests |
| v1.7.0 | Google Sheets cloud sync, auto-update from GitHub Releases, 618 tests |
| v1.6.1 | Tutorial auto-configure, market code/device ID tracking, receipt printing, settings import/export, database backups, ledger backup, data directory migration |
| v1.5.1 | First-run tutorial, single-instance prevention, PyInstaller fixes |
| v1.5.0 | Interactive tutorial overlay, production-readiness improvements |
| v1.4.1 | Custom FAM logo and window icon |
| v1.4.0 | Reports & charts, ledger backup, real seed data |
| v1.3.0 | FMNP payment integration, UI density optimization |
| v1.2.0 | UI polish — row heights, button styles, chart scaling |
