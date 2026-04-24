# FAM Market Manager — Project Instructions & Specification

> **Purpose:** This file is the single source of truth for the FAM Market Manager
> application. It is written for an AI coding assistant or a new developer who
> needs to understand, maintain, or extend the project **without** access to
> previous conversation history. Keep this file up to date with every commit.
>
> **Last updated:** 2026-04-24 — v1.9.7

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
| Tests         | pytest + pytest-qt (1591 tests)     |

---

## 2. Repository Layout

```
fam-market-manager/
├── fam/                          # Application package
│   ├── __init__.py               # __version__ = "1.9.7"
│   ├── app.py                    # Qt app entry, data dir, exception handler
│   ├── settings_io.py            # .fam file import/export
│   ├── database/
│   │   ├── connection.py         # Thread-local SQLite connection
│   │   ├── schema.py             # Table DDL + migrations (v1–v22)
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
│   │   ├── tutorial_overlay.py   # Guided tutorial + auto-configure
│   │   ├── styles.py             # Global QSS + brand colours
│   │   ├── helpers.py            # Reusable widgets & helpers
│   │   └── widgets/
│   │       ├── payment_row.py    # Payment method entry row
│   │       └── summary_card.py   # Summary display cards
│   ├── sync/
│   │   ├── base.py               # SyncResult dataclass
│   │   ├── manager.py            # SyncManager orchestration + agent tracker
│   │   ├── gsheets.py            # Google Sheets backend via gspread
│   │   ├── data_collector.py     # Collects report data + photo URLs for sync
│   │   ├── drive.py              # Google Drive photo upload (REST API)
│   │   └── worker.py             # QThread worker for background sync
│   ├── update/
│   │   ├── checker.py            # GitHub API, version comparison, download, batch script
│   │   └── worker.py             # QThread workers for check + download
│   └── utils/
│       ├── app_settings.py       # Market code, device ID, sync/update settings, key-value store
│       ├── calculations.py       # Core financial math + charge/method_amount conversion + penny reconciliation
│       ├── money.py              # Integer-cents helpers: dollars_to_cents, cents_to_dollars, format_dollars
│       ├── export.py             # CSV export + ledger backup
│       ├── logging_config.py     # Rotating file logger
│       ├── photo_storage.py      # Photo copy/resize, SHA-256 hashing, local registry
│       └── photo_paths.py        # Multi-photo JSON encode/decode
├── tests/
│   ├── test_match_formula.py         # 98 tests — formula validation, edge cases, real-world scenarios
│   ├── test_match_limit.py           # 28 tests — daily cap logic, proportional reduction, penny reconciliation under cap, cap=0/1¢ boundaries
│   ├── test_returning_customer.py    # 23 tests — multi-visit tracking
│   ├── test_adjustments.py           # 71 tests — adjustments, voids, ledger
│   ├── test_fmnp_reports.py          # 38 tests — FMNP entries and reports
│   ├── test_models.py                # 130 tests — model CRUD operations
│   ├── test_market_code.py           # 44 tests — market code, device ID
│   ├── test_backup.py                # 21 tests — backup creation + retention
│   ├── test_schema.py                # 40 tests — migrations, triggers, indexes
│   ├── test_settings_io.py           # 54 tests — import/export round-trip
│   ├── test_sync.py                  # 124 tests — cloud sync, data collection, FMNP dual-source, agent tracker
│   ├── test_update.py                # 77 tests — URL parsing, version comparison, update flow
│   ├── test_charge_conversion.py     # 52 tests — charge ↔ method_amount conversion
│   ├── test_auto_distribute.py       # 71 tests — auto-distribute payment allocation, max-cap math, cap reconciliation
│   ├── test_denomination.py          # 43 tests — denomination constraint validation
│   ├── test_multi_photo.py           # 112 tests — multi-photo storage, encoding, drive upload
│   ├── test_cloud_sync_ux.py         # 151 tests — sync UX, photo dedup (within + cross-txn), hash model
│   ├── test_money_boundaries.py      # 63 tests — integer-cents boundaries, float accumulation, FMNP check splitting, penny reconciliation
│   ├── test_reconciliation.py        # 25 tests — three-way reconciliation (DB == Ledger == Sheets)
│   ├── test_ui_payment.py            # 37 tests — payment screen UI (pytest-qt): summary cards, multi-method, stepper, auto-distribute
│   ├── test_ui_workflows.py          # 31 tests — end-to-end market day simulation, returning customer cap workflows, void exclusion
│   ├── test_ui_guards.py             # 66 tests — max-cap clamping, market day lifecycle guards, adjustment edge cases, match-cap-aware charge input
│   ├── test_ui_expanded.py           # 51 tests — production readiness: payment confirm E2E, draft save/resume, returning customer match limits, void-after-confirm, adjustment propagation, multi-receipt mixed vendors, denomination overage/forfeit, odd-cent pipeline, high-volume reconciliation, report state changes
│   └── test_payment_method_safety.py # 23 tests — payment method CRUD, deactivation safety, FMNP/FAM report separation
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

**schema_version** — version (current: 22), applied_at

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

---

## 9. Test Suite

**Run:** `python -m pytest tests/ -v` from project root

**1473 total tests across 24 files** — all must pass before committing.

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
