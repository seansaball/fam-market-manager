# FAM Market Manager — User Guide

> **For volunteers, coordinators, and market day staff**
> Version 1.4.1

---

## Welcome

FAM Market Manager helps you run a farmers market day from start to finish. You can record customer purchases, calculate FAM matching funds, track payments, and generate end-of-day reports — all from one application.

This guide walks you through everything you need to know.

---

## Getting Started

### Opening the Application

Double-click **FAM Manager.exe** to launch. The first time you open it, the app creates a database file with default markets, vendors, and payment methods already loaded.

### Understanding the Layout

The application has two main areas:

- **Sidebar (left):** A green menu with buttons for each section of the app. Click a button to switch screens.
- **Content area (right):** The active screen where you do your work.

The sidebar sections are:

| Section | What It Does |
|---------|-------------|
| **Market** | Open and close market days |
| **Receipt Intake** | Record customer purchases |
| **Payment** | Process payments and calculate FAM match |
| **FMNP Entry** | Record FMNP check entries |
| **Adjustments** | Fix mistakes in past transactions |
| **Reports** | View summaries, charts, and export data |
| **Settings** | Manage markets, vendors, and payment methods |

### Built-In Tutorial

Click the **"Start Tutorial"** button in the top-right corner at any time. A guided walkthrough will highlight each section and explain what it does. Use the **Next** and **Back** buttons to navigate, or press **Escape** to exit.

---

## The Market Day Workflow

A typical market day follows this flow:

```
Open Market Day → Record Receipts → Process Payment → (repeat) → Close Market Day
```

Here is each step in detail.

---

## 1. Open a Market Day

**Where:** Market screen (first item in the sidebar)

Before you can record any transactions, you need to open a market day.

1. Select your **market location** from the dropdown
2. Enter your **name** in the Volunteer Name field
3. Click **"Open Market Day (Today)"**

The screen will update to show the active market day with an **Open** status.

**Things to know:**
- Only one market day can be open at a time
- If a market day already exists for today's date, the app will offer to reopen it instead of creating a duplicate
- You can view past market days using the dropdown at the bottom of the screen

---

## 2. Record Customer Receipts

**Where:** Receipt Intake screen

This is where you enter each customer's purchases. Every customer gets a unique label (like **C-001**) and can have multiple receipts from different vendors.

### Adding a receipt

1. The app automatically creates a new customer (C-001, C-002, etc.)
2. Select the **vendor** from the dropdown
3. Enter the **receipt total** (the dollar amount on the paper receipt)
4. Optionally enter a **zip code** and any **notes**
5. Click **"Add Receipt to Order"**

A confirmation message appears briefly, and the receipt is added to the customer's order.

### Multiple receipts per customer

If a customer bought from more than one vendor, keep adding receipts. They all group under the same customer label. You can see the running list and order total in the **Receipts for Customer** section.

### Moving to payment

When you have entered all receipts for this customer, click **"Confirm All — Proceed to Payment"** at the bottom of the screen. This takes you to the Payment screen.

### Returning customers

If a customer comes back later in the day for another purchase:

1. Use the **"Returning Customer"** dropdown at the top of the screen
2. Select their customer label (e.g., C-001)
3. Add their new receipts as usual

The payment screen will remember how much FAM match they have already used today.

### Pending orders

If you need to set aside an order and come back to it later, it will appear in the **Pending Orders** section at the bottom. You can **Resume** (go to payment), **Add Receipt** (add more items), or **Delete** the order.

---

## 3. Process Payment

**Where:** Payment screen

After confirming receipts, the Payment screen shows the customer's order summary and lets you enter how they are paying.

### Understanding the summary cards

At the top of the screen you will see:

| Card | What It Shows |
|------|--------------|
| **Customer / Order** | Customer label, market name, and receipt total |
| **Total Allocated** | How much you have entered so far across all payment methods |
| **Remaining** | How much is left to allocate (should reach $0.00) |
| **Customer Pays** | What the customer owes after FAM match |
| **FAM Match** | How much FAM is covering |

### Adding payment methods

1. Click **"+ Add Payment Method"**
2. Select the payment type (SNAP, FMNP, Cash, etc.)
3. Enter the **amount** for that payment method
4. The app automatically calculates the **FAM Match** and **Customer Pays** amounts

You can add multiple payment methods if the customer is splitting their payment.

### Confirming payment

When the **Remaining** card shows **$0.00**:

1. Click **"Confirm Payment"**
2. Review the collection summary in the popup
3. Click **Yes** to confirm

The app records the payment, generates transaction IDs, and returns you to Receipt Intake for the next customer.

### Save as Draft

If you need to pause and come back to this payment later, click **"Save as Draft"** instead. The order will appear in the Pending Orders list on the Receipt Intake screen.

### Daily match limits

Some markets set a maximum FAM match per customer per day. If a customer reaches their limit, the app will display a warning message and automatically cap the match amount. The summary cards will update to reflect the reduced match.

---

## 4. FMNP Check Tracking

**Where:** FMNP Entry screen

Use this screen to record FMNP (Farmers Market Nutrition Program) checks received from vendors. These are tracked separately from regular transactions.

1. Select the **market day** from the dropdown
2. Select the **vendor**
3. Enter the **dollar amount**
4. Optionally enter the **check count** and any **notes**
5. Enter **your name** in the Entered By field
6. Click **"Add FMNP Entry"**

The entry appears in the table below. You can **Edit** or **Delete** entries using the buttons in the Actions column.

---

## 5. Fix Mistakes (Adjustments)

**Where:** Adjustments screen

If you need to correct a transaction after it was confirmed, use the Adjustments screen.

### Finding a transaction

Use the filters at the top:
- **Market:** Filter by market location
- **Status:** Filter by Draft, Confirmed, Adjusted, or Voided
- **Transaction ID:** Search by the FAM transaction ID (e.g., FAM-20260301-0001)

Click **"Search"** to find matching transactions.

### Adjusting a transaction

1. Find the transaction in the results table
2. Click **"Adjust"** in the Actions column
3. In the dialog, you can change:
   - **Receipt Total** — correct the dollar amount
   - **Vendor** — change which vendor the receipt belongs to
   - **Reason** — select why you are making the change
   - **Notes** — explain the adjustment
   - **Adjusted By** — enter your name
4. Click **OK** to save

The adjustment is recorded in the audit log with the old and new values.

### Voiding a transaction

If a transaction should be completely cancelled:

1. Click **"Void"** in the Actions column
2. Confirm the action in the popup

Voided transactions remain visible in the system but are marked as voided and excluded from reports and totals.

### Audit log

The bottom of the screen shows a **Recent Audit Log** table with the most recent changes. This provides a clear record of who changed what and when.

---

## 6. Reports and Exports

**Where:** Reports screen

The Reports screen provides several views of your data, each in its own tab.

### Available reports

| Tab | What It Shows |
|-----|--------------|
| **Summary** | Overview metrics and charts for the selected period |
| **Detailed Ledger** | Every transaction with full payment breakdowns |
| **Vendor Reimbursement** | How much each vendor is owed (receipt totals, FAM subsidy, customer paid) |
| **FAM Match Report** | FAM match amounts by customer |
| **Geolocation** | Customer zip code analysis and heat map |
| **Activity Log** | Detailed audit trail of all actions taken |
| **Error Log** | System error entries for troubleshooting |

### Filtering

Use the controls at the top of the screen to narrow your view:
- **Date range:** Select specific dates or view all dates
- **Market:** Check/uncheck specific markets
- **Vendor:** Check/uncheck specific vendors
- **Payment Method:** Check/uncheck specific payment types

### Exporting data

Each report tab has an **Export** button that saves the data as a CSV file. The file name is automatically generated with a timestamp.

---

## 7. Settings

**Where:** Settings screen

Use the Settings screen to manage the reference data used throughout the application.

### Markets tab

- **Add a market:** Enter the name and optional address, then click "Add Market"
- **Edit:** Change the market name or address
- **Vendors:** Choose which vendors serve this market
- **Payments:** Choose which payment methods this market accepts
- **Match Limit:** Set the maximum FAM match per customer per day
- **Limit On/Off:** Enable or disable the daily match limit
- **Activate/Deactivate:** Make a market available or unavailable for new market days

### Vendors tab

- **Add a vendor:** Enter the name and optional contact info, then click "Add Vendor"
- **Edit:** Change the vendor name or contact info
- **Markets:** Choose which markets this vendor serves
- **Activate/Deactivate:** Make a vendor available or unavailable for new receipts

### Payment Methods tab

- **Add a payment method:** Enter the name and match percentage, then click "Add Payment Method"
- **Edit:** Change the name or match percentage
- **Reorder:** Use the up/down arrows to change the display order
- **Activate/Deactivate:** Make a payment method available or unavailable

### Reset tab

The Reset tab allows you to erase all data and restore the original default markets, vendors, and payment methods. This requires two confirmations to prevent accidental data loss.

> **Warning:** Resetting deletes all market days, transactions, FMNP entries, and audit log entries permanently.

---

## Understanding Payment Matching

The FAM matching system is the core of what this application calculates. Here is how it works in simple terms:

**Each payment method has a match percentage.** This determines how much of each dollar FAM covers versus what the customer pays.

| Payment Method | Match % | What It Means |
|---------------|---------|--------------|
| SNAP | 100% | FAM matches dollar-for-dollar. Customer pays half, FAM pays half. |
| FMNP | 100% | Same as SNAP — dollar-for-dollar match. |
| Cash | 0% | No match. Customer pays the full amount. |

**Example:** A customer buys $20 of produce and pays with SNAP (100% match).
- Customer pays: **$10.00**
- FAM match: **$10.00**
- Vendor receives the full $20.00

**Mixed payment example:** A customer buys $30 and pays $20 with SNAP (100% match) and $10 with Cash (0% match).
- SNAP portion: Customer pays $10, FAM matches $10
- Cash portion: Customer pays $10, FAM matches $0
- **Customer total: $20.00** | **FAM total: $10.00**

---

## Your Data

### Where data is stored

All data is saved in a file called **fam_data.db** in the same folder as the application. This one file contains everything — market days, transactions, customer orders, payment records, and the audit log.

### Backing up your data

To back up, simply copy the `fam_data.db` file to a safe location (USB drive, cloud storage, etc.).

### Moving to another computer

Copy the entire FAM Manager folder (including the database file) to the new computer. Everything will work exactly as before.

### Ledger backup

The app automatically maintains a text-based backup file called `fam_ledger_backup.txt` in the same folder. This is a human-readable summary of the most recent market day that can be opened in any text editor, even if the application is not available.

---

## Troubleshooting

### The app will not open

Make sure you **extracted the zip file** before running. Right-click the zip file and choose "Extract All." Do not run the `.exe` directly from inside the zip.

### Windows SmartScreen warning

When you first run the app, Windows may show a SmartScreen warning. Click **"More info"** and then **"Run anyway."** This is normal for applications that are not distributed through the Microsoft Store.

### "No active market day" messages

You need to open a market day before recording transactions. Go to the **Market** screen, select a market, enter your name, and click **"Open Market Day."**

### Payment does not balance

If the Remaining card shows a number other than $0.00, adjust the payment method amounts until the total allocated matches the receipt total exactly.

### Something went wrong

Check the log file (`fam_manager.log`) in the application folder for details. Share this file with your coordinator or technical support if you need help.

---

## Quick Reference

### Keyboard Shortcuts (Tutorial)

| Key | Action |
|-----|--------|
| **Right Arrow** or **Enter** | Next step |
| **Left Arrow** | Previous step |
| **Escape** | Close tutorial |

### Transaction Status Colors

| Status | Meaning |
|--------|---------|
| **Draft** | In progress, not yet confirmed |
| **Confirmed** | Payment processed, finalized |
| **Adjusted** | Corrected after confirmation |
| **Voided** | Cancelled, excluded from totals |

### Common Transaction ID Format

Transaction IDs follow the pattern: **FAM-YYYYMMDD-NNNN**

Example: `FAM-20260301-0005` means the 5th transaction on March 1, 2026.

### Customer Label Format

Customer labels follow the pattern: **C-001**, **C-002**, etc.

These reset at the start of each new market day.
