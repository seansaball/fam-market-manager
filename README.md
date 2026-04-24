# FAM Market Manager

A desktop application for managing Food Assistance Match (FAM) market day transactions. Built for volunteers to record customer purchases, track payment methods, calculate FAM matching funds, and generate end-of-day reports.

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

1473 tests across 24 test files covering formula validation, match limits, returning customers, transaction adjustments, FMNP reports, market codes, device IDs, backups, schema migrations, settings import/export, cloud sync, auto-update, charge conversion, denomination validation, photo storage, multi-photo workflows, photo deduplication, FMNP sync integration, integer-cents boundary validation, three-way reconciliation (DB/Ledger/Sheets), automated UI testing (payment screen, workflows, market-day simulation), max-cap clamping, market day lifecycle guards, adjustment edge cases, payment method CRUD safety, match-cap-aware charge input edge cases, and end-to-end production readiness tests (payment confirmation pipelines, draft save/resume, returning customer match limits, void-after-confirm exclusion, adjustment propagation, multi-receipt mixed vendors, denomination overage/forfeit, odd-cent reconciliation, high-volume market day simulation, report-screen state changes).

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
