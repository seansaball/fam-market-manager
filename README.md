# FAM Market Day Transaction Manager

A desktop application for Food Assistance Match (FAM) to manage farmers market transactions, payment processing, and vendor reimbursements.

## Setup

### Requirements
- Python 3.11+
- pip

### Install Dependencies
```bash
cd fam-market-manager
pip install -r requirements.txt
```

### Run the Application
```bash
python run.py
```

The application will create a `fam_data.db` SQLite database file on first launch and populate it with sample data.

## Usage

1. **Market Day Setup** - Open a new market day for a market location
2. **Receipt Intake** - Record customer receipts and generate transaction IDs
3. **Payment Processing** - Allocate payment methods with automatic discount calculations
4. **FMNP Entry** - Record Farmers Market Nutrition Program checks
5. **Adjustments** - Correct or void transactions with full audit trail
6. **Reports** - View and export vendor reimbursement, subsidy, and ledger reports
7. **Settings** - Manage markets, vendors, and payment methods

## Packaging (Future)

To create a standalone executable with PyInstaller:
```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "FAM Manager" --add-data "fam;fam" run.py
```
The executable will be in the `dist/` folder. The SQLite database will be created alongside the executable on first run.
