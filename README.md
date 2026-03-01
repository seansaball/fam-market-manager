# FAM Market Manager

A desktop application for managing Food Assistance Match (FAM) market day transactions. Built for volunteers to record customer purchases, track payment methods, calculate FAM matching funds, and generate end-of-day reports.

## Features

- **Market Day Management** — Open/close market days and track volunteer shifts
- **Receipt Intake** — Record customer purchases with vendor, amount, and payment method
- **Customer Orders** — Group multiple receipts per customer visit with automatic FAM match calculation
- **Payment Matching** — Configurable match percentages per payment method (SNAP, FMNP, Food RX, etc.)
- **Daily Match Limits** — Per-market caps on matching funds per customer
- **Reports & Charts** — Revenue breakdowns, vendor summaries, and payment method analytics
- **Admin Adjustments** — Edit, adjust, or void transactions with full audit logging
- **Settings** — Manage markets, vendors, and payment methods
- **Data Export** — CSV exports and automatic ledger backups

## Installation

1. Download the latest `FAM-Manager-vX.X.X.zip` from [Releases](https://github.com/seansaball/fam-market-manager/releases)
2. Extract the zip to any folder
3. Double-click **FAM Manager.exe** to run

No Python installation required. Works on Windows 10/11 (64-bit).

## Development Setup

```bash
git clone https://github.com/seansaball/fam-market-manager.git
cd fam-market-manager

python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt

python run.py
```

## Building the Executable

```bash
build.bat
```

Output: `dist\FAM Manager\FAM Manager.exe`

## Tech Stack

- **Python 3.12** + **PySide6** (Qt6)
- **SQLite** for local data storage
- **Matplotlib** for charts
- **PyInstaller** for standalone packaging
