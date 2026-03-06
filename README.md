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
- **FMNP Check Tracking** — Record and manage Farmers Market Nutrition Program entries
- **Settings Import/Export** — Share market configurations across devices via `.fam` files
- **Multi-Market Identity** — Auto-derived market codes and device IDs in transaction IDs, exports, and filenames
- **Automatic Backups** — Periodic database backups with 20-file retention, plus human-readable ledger backup
- **Data Export** — CSV exports with market code and device ID columns for finance team consolidation
- **First-Run Tutorial** — Interactive guided walkthrough with one-click auto-configure option

## Installation

1. Download the latest `FAM_Manager_vX.X.X.zip` from [Releases](https://github.com/seansaball/fam-market-manager/releases)
2. Extract the zip to any folder
3. Double-click **FAM Manager.exe** to run
4. Follow the tutorial — on the final step, click **"Yes — Load Default Data"** to auto-configure 3 markets, 8 vendors, and 6 payment methods

No Python installation required. Works on Windows 10/11 (64-bit).

> **Note:** Windows SmartScreen may prompt on first run. Click "More info" then "Run anyway."

## Upgrading

Your data is stored separately in `%APPDATA%\FAM Market Manager\`, so upgrading is safe and simple:

1. Download the new zip
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

479 tests across 10 test files covering formula validation, match limits, returning customers, transaction adjustments, FMNP reports, market codes, device IDs, backups, schema migrations, and settings import/export.

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
- **PyInstaller** for standalone packaging

## Version History

| Version | Summary |
|---------|---------|
| v1.6.1 | Tutorial auto-configure, market code/device ID tracking, receipt printing, settings import/export, database backups, ledger backup, data directory migration |
| v1.5.1 | First-run tutorial, single-instance prevention, PyInstaller fixes |
| v1.5.0 | Interactive tutorial overlay, production-readiness improvements |
| v1.4.1 | Custom FAM logo and window icon |
| v1.4.0 | Reports & charts, ledger backup, real seed data |
| v1.3.0 | FMNP payment integration, UI density optimization |
| v1.2.0 | UI polish — row heights, button styles, chart scaling |
