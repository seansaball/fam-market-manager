# FAM Market Manager — User Guide

> **For volunteers, coordinators, and market day staff**
> Version 2.0.7

---

## Welcome

FAM Market Manager helps you run a farmers market day from start to finish. You can record customer purchases, calculate FAM matching funds, track payments, attach receipt photos, and generate end-of-day reports — all from one application.

This guide walks you through everything you need to know.

## Companion documents

This is the long-form manual. There are three other documents you should know about:

| Document | Who it's for | When to use |
|---|---|---|
| **`EMERGENCY_RUNBOOK.md`** | Volunteers at the booth | Print this. When something breaks during a market day, find your symptom in the runbook and follow the steps. |
| **`COORDINATOR_HANDBOOK.md`** | The person responsible for laptops, credentials, and rollouts | Setup, multi-laptop deployments, monthly reconciliation, escalation guidance. |
| **`QUICK_REFERENCE.md`** | Volunteers at the booth | One-page cheat sheet. Print and tape behind the booth. |
| **In-app Help** (sidebar) | Everyone, anytime | Articles, troubleshooting flows, and a live diagnostic. Search in plain English. |

If you're a volunteer reading this on a phone in the middle of a problem, **stop** and use the Emergency Runbook or in-app Help instead — both are designed for that exact moment. This guide is more useful for learning the app between markets.

---

## Getting Started

### First Launch

Double-click **FAM Manager.exe** to launch. On first launch:

1. The app creates a data folder at `%APPDATA%\FAM Market Manager\`
2. A guided tutorial automatically starts, walking you through every section
3. On the final tutorial step, you'll see **"Quick Setup"** — click **"Yes — Load Default Data"** to auto-configure 3 markets, 23 vendors, and 6 payment methods
4. If you prefer, click **"No Thanks — Start Blank"** to configure everything manually or import a `.fam` settings file

> **Windows SmartScreen:** On first run, Windows may show a warning. Click **"More info"** then **"Run anyway."**

### Understanding the Layout

The application has two main areas:

- **Sidebar (left):** A green menu with buttons for each section. Click a button to switch screens.
- **Content area (right):** The active screen where you do your work.

The sidebar sections are listed in the same order as in the app:

| Section | What It Does |
|---------|-------------|
| **Market** | Open and close market days |
| **Receipt Intake** | Record customer purchases |
| **Payment** | Process payments and calculate FAM match |
| **Adjustments** | Fix mistakes in past transactions |
| **FMNP Entry** | Record FMNP checks at the vendor side |
| **Reports** | View summaries, charts, and export data |
| **Settings** | Manage markets, vendors, payment methods, **rewards**, cloud sync, and updates |
| **Help** | Articles, troubleshooting, and a live diagnostic snapshot |

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

The screen will update to show the active market day with an **Open** status. The title bar will show the market code in brackets (e.g., `[BPFM]`).

**Things to know:**
- Only one market day can be open at a time
- If a market day already exists for today's date, the app will offer to reopen it instead of creating a duplicate
- A market code is automatically derived from the market name (e.g., "Bethel Park Farmers Market" → `BPFM`)
- Automatic database backups run every 5 minutes while a market day is open
- **You must have an open market day to record transactions.** If the market day is closed, the app will block new receipts and payments until you reopen it or open a new one

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
| **Customer Forfeit** | (v2.0.7+) Token face value the customer over-tendered when a denominated unit (Food RX, Food Bucks) was bigger than the receipt remaining. $0.00 in normal use; non-zero only when the customer hands a $10 token for a $6.52 receipt and the FAM match couldn't fully absorb the gap. |

### Adding payment methods

1. Click **"+ Add Payment Method"**
2. Select the payment type (SNAP, FMNP, Cash, etc.)
3. Enter the **customer charge** — the amount the customer is paying with this method. The app automatically computes the corresponding FAM match and the total allocated based on the method's match percentage.
4. The summary cards update in real time as you enter amounts

You can add multiple payment methods if the customer is splitting their payment.

**Denomination validation:** For payment methods that use fixed denominations (such as FMNP checks), the app validates that the amount you enter is a valid multiple of the denomination. If you enter an amount that does not divide evenly, a warning appears and the entry is blocked until corrected.

**Automatic balance clamping:** As you enter payment amounts, the app automatically limits each payment method's maximum to the remaining order balance. The stepper's + button disables when one more unit would exceed the remaining amount, and free-text entry fields cap at the remaining balance. This prevents over-allocation before it happens.

### The per-row ⚡ toggle (v2.0.7+)

Each non-denominated payment row (SNAP, Cash, etc.) has a small **⚡ icon** next to the amount field that controls whether that row participates in **Auto-Distribute**. Two states:

| Visual | State | Meaning |
|---|---|---|
| **Green ⚡** | Active | Auto-Distribute will fill or refill this row. The row is the "overflow target" that absorbs the remainder. |
| **Grey ⚡** | Locked | Auto-Distribute will skip this row. The volunteer's typed value stays exactly as entered, even when the daily FAM match cap kicks in. |

**Automatic transitions** (no clicks needed for the common case):

* **Typing into the amount field locks the row.** As soon as you type a value (e.g. "$125"), the icon flips to grey. This is the default "I know exactly how much SNAP the customer has on their card" behavior.
* **Adding a row when an Active row already exists defaults the new row to Locked.** Only one non-denom row at a time can be the overflow target — if you add a third method, it comes in Locked at $0.00 and you can either type a value or click the ⚡ to activate.

**Manual toggle**:

* **Click a grey ⚡** to activate the row (Auto-Distribute will refill it on the next click). The previously-active row automatically locks so there's still exactly one overflow target.
* **Click a green ⚡** to lock the current value (pin it where it is).

**When to use each state**:

* **Customer has a fixed amount on one method, rest in cash**: Type SNAP $125 (auto-locks), let the green ⚡ Cash row absorb the rest via Auto-Distribute.
* **Volunteer wants to manually balance everything**: Lock all rows by typing values; the engine respects every typed amount.
* **Customer wants to maximize FAM match without specifying amounts**: Leave one row green (Active) and click Auto-Distribute — the engine fills the green row with whatever absorbs the receipt.

**Why this matters**: Pre-v2.0.7, Auto-Distribute would silently inflate any row's value if the daily match cap shrank the FAM contribution. The volunteer would type "$125 SNAP" (because that's all the customer has on their EBT card), click Auto-Distribute, and see SNAP magically become "$138.09" — confusing and unfixable without deleting the row. The ⚡ toggle makes intent explicit and gives volunteers a clear way to say "this value is final."

### Attaching a receipt photo

You can attach a photo of the customer's physical receipt or check to any payment entry. Click the camera icon next to a payment method row to capture or select a photo. The photo is stored locally and will upload to Google Drive during the next cloud sync (see the Cloud Sync section below).

### Confirming payment

When the **Remaining** card shows **$0.00**:

1. Click **"Confirm Payment"**
2. Review the collection summary in the popup
3. Click **Yes** to confirm

The app records the payment, generates transaction IDs (e.g., `FAM-BPFM-20260306-0001`), and returns you to Receipt Intake for the next customer.

### Printing a receipt

After payment is confirmed, a **"Print Receipt"** button appears. Click it to print a customer receipt showing:
- Market name and date
- Vendor listing with per-vendor totals
- Payment method breakdown (amount, FAM match, customer paid)
- Grand totals

### Save as Draft

If you need to pause and come back to this payment later, click **"Save as Draft"** instead. The order will appear in the Pending Orders list on the Receipt Intake screen.

### Daily match limits

Some markets set a maximum FAM match per customer per day. If a customer reaches their limit, the app will display a warning message and automatically cap the match amount. The summary cards will update to reflect the reduced match.

---

## 4. FMNP Check Tracking

**Where:** FMNP Entry screen

Use this screen to record FMNP (Farmers Market Nutrition Program) checks
that vendors took at the booth.  These are recorded separately from
regular transactions because the **vendor applied the match themselves**
at their booth (treating a $5 FMNP check as $10 worth of food).

**How the reimbursement works:**

- The vendor cashes the original FMNP check directly with the FMNP
  program — they get the face value back ($5).
- **FAM reimburses the same face value** ($5) at end-of-month so the
  vendor is made whole on the match they gave away at the booth.
- The amount logged here appears as **FMNP (External)** in the Vendor
  Reimbursement report and **is included in the Total Due to Vendor**.
- FAM does **not** add a match percentage on top — the vendor already
  did at the booth.

1. Select the **market day** from the dropdown
2. Select the **vendor**
3. Enter the **dollar amount** (must be a multiple of $5 — the FMNP
   denomination)
4. Optionally enter the **check count** and any **notes**
5. Enter **your name** in the Entered By field
6. Click **"Add FMNP Entry"**

The entry appears in the table below. You can **Edit** or **Delete**
entries using the buttons in the Actions column.  All edits and
deletions are written to the audit log with old + new values.

### "All Market Days" filter (v2.0.7+)

The market-day dropdown defaults to **"All Market Days"**, which is a
**browse-only filter** for searching existing FMNP entries across the
full history (paired with the date-range filter on the same screen).

When "All Market Days" is selected, the **"Add FMNP Entry"** button
greys out and an inline hint label appears next to it:

> ⚠ ← Pick a specific market day above to add a new entry

This is intentional — you can't attribute a new entry to "all markets,"
the entry needs a single concrete market day. To add a new entry, pick
a specific date from the dropdown; the button enables and the hint
disappears.

### Attaching check photos

You can attach photos of FMNP checks directly to each entry. The app provides **multi-photo support** — the number of available photo slots is automatically calculated based on the dollar amount divided by the check denomination. For example, if you enter $30 and the denomination is $5, six photo slots appear for you to attach one photo per check.

When the number of checks is large, the photo attachment area becomes **scrollable** so it does not crowd the rest of the form.

### Photo deduplication

The app uses a 3-layer deduplication system to prevent the same check photo from being recorded twice:

- **Within-entry block:** The same photo cannot be attached to two slots within the same FMNP entry.
- **Cross-transaction warning:** If a photo matches one already used in a different transaction, the app warns you before allowing it.
- **Drive upload reuse:** During cloud sync, if an identical photo already exists in Google Drive, the existing file is reused instead of uploading a duplicate.

---

## 5. Fix Mistakes (Adjustments)

**Where:** Adjustments screen

If you need to correct a transaction after it was confirmed, use the Adjustments screen.

### Finding a transaction

Use the filters at the top:
- **Market:** Filter by market location
- **Last Updated:** Date range picker — filters by the **most recent activity** (creation OR adjustment) on each transaction.  Set it to today to see what you worked on today, even if the underlying market day was months ago.
- **Status:** Filter by Draft, Confirmed, Adjusted, or Voided
- **Transaction ID:** Search by the FAM transaction ID (e.g., FAM-BPFM-20260306-0001)

The Last Updated filter is **live** — change the range and the table refreshes immediately.  The other filters need a Search button click.

The results table shows three dates per row, each with a different meaning:

- **Market Date** — the business day this transaction's revenue belongs to (used for vendor reimbursement and the FAM Match Report)
- **Created** — when this transaction was first entered into the app
- **Last Updated** — the most recent activity (the Last Updated filter targets this column)

### Adjusting a transaction

1. Find the transaction in the results table
2. Click **"Adjust"** in the Actions column
3. In the dialog, you can change:
   - **Receipt Total** — correct the dollar amount
   - **Vendor** — change which vendor the receipt belongs to
   - **Reason** — select why you are making the change
   - **Notes** — explain the adjustment
   - **Adjusted By** — enter your name
   - **Payment breakdown** — adjust how each payment method contributed; the row inputs **cap at the receipt total** so you can't accidentally over-allocate
4. Click **OK** to save

The adjustment is recorded in the audit log with the old and new values.

### Customer-gone path (when the customer can't pay more)

Most adjustments happen **after** the customer has left the market — coordinators reconcile vendor receipts hours or days later.  When the adjustment would require the customer to physically pay more than they originally did, a popup appears asking:

> "Can the customer still be charged?"

Three scenarios trigger it:

1. **Receipt total raised** but breakdown not updated — vendor reconciliation showed a higher total
2. **Customer payment increased** in the breakdown — you raised a payment row's amount (e.g. correcting an under-recorded count of physical Food Bucks)
3. **Denomination overage** — physical instruments (FMNP checks, Food Bucks) overshoot the receipt by less than one full unit

**Click Yes** if you can still charge the customer (or if they did pay the additional amount and you're correcting under-recorded data).  Save proceeds normally.

**Click No** if the customer is unavailable.  The system records what they ACTUALLY paid (the original amount) and adds an **Unallocated Funds** line item for the gap — FAM absorbs that amount.  The vendor still gets reimbursed in full so they're never short.  All Unallocated Funds activity is visible in:

- **Audit Log** — every absorption gets a dedicated `UNALLOCATED_FUNDS` action with the dollar amount
- **FAM Match Report** — new "FAM Absorbed" column + summary card alongside "FAM Match"
- **Vendor Reimbursement** and **Detailed Ledger** — Unallocated Funds appears as its own per-method column

### Denomination forfeit (Adjustments)

If you're recording physical instruments (Food Bucks tokens, FMNP checks) and they overshoot the receipt — for example, the customer handed over 3 × $5 FMNP checks ($15 face value) against an $11 receipt — a popup asks you to confirm the **forfeit**.  FAM caps its match at what fits the receipt, the customer "forfeits" the unmatched portion of the FAM match they would have gotten, the vendor still gets paid the full receipt, and the customer still hands over the physical checks (you can't make change against them).  The audit log records the forfeit amount.

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
| **Vendor Reimbursement** | How much each vendor is owed (receipt totals, FAM subsidy, customer paid). The shared Google Sheet version (v2.0.9+) emits one row per (market × vendor × month) so coordinators can reconcile month-over-month. |
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

### Resizable columns

All report tables automatically fit their columns to the content width. You can also **manually drag column borders** to resize them to your preference. This applies to every report tab.

### Exporting data

Each report tab has an **Export** button that saves the data as a CSV file. The file name is automatically generated with a timestamp and market code (e.g., `fam_BPFM_vendor_reimbursement_20260306_140530.csv`).

All CSV exports include `market_code` and `device_id` as the first two columns, allowing the FAM finance team to identify which market and device generated each report.

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

> **About FMNP (v1.9.8+):** FMNP is now a togglable payment method like the others, **and it defaults to inactive on fresh installs / Load Defaults**.  When inactive, it does NOT appear in the Payment Screen's payment-method dropdown, but the dedicated **FMNP Entry** screen continues to work normally regardless.  Most markets leave FMNP inactive here because they handle FMNP exclusively through the FMNP Entry screen (vendor-matched at the booth).  Activate FMNP only if your market wants to record FMNP-as-a-payment-method on the customer's order at the FAM table.

### Rewards tab

Customer rewards (tokens, vouchers, extra dollars) given on top of the FAM match. Configured by the coordinator; volunteers see only the result on the payment confirmation dialog and the printed receipt.

- **Master toggle:** Enable / disable all rewards. When off, no GIVE TO CUSTOMER zone appears on the confirmation dialog.
- **Rules table:** Each row defines one rule (trigger method, threshold, reward method, reward amount, active toggle).
- **Add Rule / Edit / Delete:** standard CRUD.

See the **Rewards Program** section near the end of this guide for the full math, what gets recorded where, and how to handle voids that involved physical token hand-offs. See also the in-app help articles `rewards-overview`, `rewards-configure`, and `rewards-given-then-voided`.

### Preferences tab

- **Device Identity:** Shows the auto-derived market code and device ID (read-only)
- **Large Receipt Threshold:** Configure the warning threshold for unusually large receipts

### Cloud Sync tab

One-way sync that uploads end-of-day reports to Google Sheets so coordinators and the finance team can view data remotely. Data flows from the app to Google Sheets only — changes made in the spreadsheet are not pulled back into the app.

**Setting up sync:**

1. Obtain a Google service account credentials file (JSON) from your coordinator
2. Go to **Settings → Cloud Sync**
3. Click **"Load Credentials"** and select the JSON file
4. Enter the **Spreadsheet ID** (the long string in the Google Sheet URL)
5. Click **"Save Sync Settings"**

**Running a sync:**

1. Click **"Sync Now"** on the Cloud Sync tab
2. The app uploads the current day's data to the configured spreadsheet
3. A progress indicator shows while the sync is running
4. On success, the "Last Synced" timestamp updates

**What the sync indicator in the sidebar means:**

The small colored dot + label next to the "Sync to Cloud" button reflects the
current state of cloud sync. It does **not** make a live internet speed test —
it reports what the app knows for certain.

| Dot color | Label | What it means |
|-----------|-------|---------------|
| Green | **Last sync OK** | Your most recent sync attempt succeeded |
| Red | **Sync failed** | The most recent sync attempt hit an error |
| Amber | **Syncing…** | A sync is currently in progress |
| Amber | **Attention** | Last sync succeeded but one or more photos had issues |
| Gray | **No network** | Windows reports the laptop is disconnected from the network |
| Gray | **Not synced yet** | Sync is configured but no sync has run yet on this laptop |
| (hidden) | | Sync is not configured |

If you see "No network", your data is still safe — it is stored locally on
this laptop and will sync automatically the next time the laptop reconnects
and a sync is run.

**Google Drive photo sync:**

When cloud sync is configured, all receipt and FMNP check photos are automatically uploaded to Google Drive during each sync. Photos are organized into folders by market and date. Uploaded photos inherit the permissions of their parent folder — they are not shared publicly.

If a photo is deleted or trashed from Google Drive, the app detects this on the next sync cycle and automatically re-uploads the missing file. Stale URL references are cleaned up so re-uploads happen reliably.

**FMNP Entries sync:**

FMNP data is synced from both the FMNP Entry screen and the Payment flow. Each check produces one row in the spreadsheet, with individual photo links and Transaction IDs included for full traceability.

**Agent tracker:**

Each device reports its sync activity to a dedicated tracker tab in Google Sheets. This allows coordinators to monitor which devices have synced and when, on a per-device basis.

**Retry behavior:**

Cloud sync uses exponential backoff when it encounters transient Google API errors (such as rate limits or temporary outages). The sync will retry automatically several times before reporting a failure.

> **Note:** Sync requires an internet connection. If it fails after all retries, the error is displayed and your local data is unaffected.

### Updates tab

Check for new versions of the application and install them directly from the app.

**Checking for updates:**

1. Go to **Settings → Updates**
2. The repository URL defaults to the official FAM Market Manager repository
3. Click **"Check for Updates"**
4. The app contacts GitHub and compares your current version against the latest release
5. If an update is available, the version and release notes are displayed

**Installing an update:**

1. Click **"Download & Install"** (only enabled when an update is available)
2. Confirm the update in the dialog
3. The app downloads the new version, verifies the file, and restarts automatically
4. Your data is never affected — it lives separately in `%APPDATA%`

**Auto-check on launch:**

- By default, the app checks for updates automatically 5 seconds after launch (once per 24 hours)
- If an update is found, a notification appears — you can update now or dismiss it
- Disable auto-check by unchecking **"Auto-check for updates on launch"** on the Updates tab

> **Note:** The "Download & Install" button is disabled while a market day is open, to prevent interrupting active transactions.

### Import & Export Settings

- **Export Settings:** Click the **Export** button at the top of the Settings screen to save your current markets, vendors, and payment methods to a `.fam` file. This is useful for backing up your configuration or sharing it with another machine.
- **Import Settings:** Click the **Import** button to load markets, vendors, and payment methods from a `.fam` file. The app will show you a preview of what will be imported and skip any items that already exist.

A default settings file (`FAM_Default_Settings.fam`) is included with the application if you prefer manual import over the tutorial auto-configure.

### Reset tab

The Reset tab allows you to erase all data and start with a clean slate. This requires two confirmations to prevent accidental data loss.

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

All data is saved in your Windows **AppData** folder:

```
%APPDATA%\FAM Market Manager\
├── fam_data.db             <- your database (all transactions, settings, etc.)
├── fam_ledger_backup.txt   <- auto-generated human-readable ledger backup
├── fam_manager.log         <- application log file
├── sync_credentials.json   <- Google Sheets credentials (if cloud sync configured)
└── backups/                <- automatic database backups (20 most recent)
```

You can quickly open this folder from the app: click the **About** button in the sidebar, then click **"Open Data Folder"**.

Because your data lives separately from the application, you can safely upgrade to a new version by simply replacing the application folder — your data will not be affected.

### Automatic backups

The app automatically creates database backups:
- When a market day is opened
- When a market day is closed
- Every 5 minutes while a market day is active

Backups are stored in the `backups/` subfolder. The 20 most recent backups are kept; older ones are automatically deleted.

### Ledger backup

The app automatically maintains a text-based backup file called `fam_ledger_backup.txt` in the data folder. This is a human-readable summary of **all transactions from every market day** that can be opened in any text editor, even if the application is not available. It is updated after every payment confirmation, adjustment, void, and market-day close.

### Backing up your data

To back up, copy the `fam_data.db` file from your data folder to a safe location (USB drive, cloud storage, etc.).

### Moving to another computer

1. Install FAM Manager on the new computer (extract the zip and run once)
2. Copy `fam_data.db` from the old computer's `%APPDATA%\FAM Market Manager\` folder to the same location on the new computer
3. Launch the app — everything will be exactly as before

### Upgrading to a new version

**Option A — In-App Auto-Update (recommended):**

1. Go to **Settings → Updates** and click **"Check for Updates"**
2. If an update is available, click **"Download & Install"**
3. The app downloads the new version, restarts automatically, and you are done

**Option B — Manual:**

1. Download the new `FAM_Manager_vX.X.X.zip` from the GitHub Releases page
2. Delete the old application folder (or extract over it)
3. Launch the new version — it will find your existing data automatically

Your database, log file, and ledger backup are never inside the application folder, so upgrading is completely safe.

---

## In-App Help (the Help sidebar item)

As of v1.9.8, the app has a built-in **Help** screen — click **Help** in
the sidebar.  It has four tabs:

- **Walkthrough** — an animated training overview ("Your Day at the
  Market") that walks new volunteers through the entire market-day
  cycle in five stages.  Loops in place; click **Next** to move on
  when ready (the button gently pulses gold to remind you).
- **Browse** — over 50 categorized help articles with live search.
  Type any term ("FMNP", "returning customer", "sync") and the list
  filters immediately.  Each article links to related articles.
- **Troubleshooting** — symptom-based guides ("Sync is red", "Photo
  isn't uploading", "App is slow") with step-by-step actions.
- **System Status** — a live diagnostic snapshot of this laptop:
  app version, last sync, disk usage, record counts, and a
  **Copy Diagnostic Info** button that puts everything on your
  clipboard so you can paste it into a coordinator email.

If you're new to the app, start with the Walkthrough.  If you have a
specific question, search the Browse tab.  If something seems broken,
go straight to Troubleshooting.

---

## Troubleshooting

> **For market-day emergencies, use `EMERGENCY_RUNBOOK.md`.** It's
> shorter, symptom-indexed, and designed to read on a phone in
> 30 seconds. The notes below are a short summary; the runbook
> goes deeper.

### Quick lookup

| Symptom | Where to look |
|---|---|
| App won't open | Emergency Runbook §1 |
| "Already running" message | Emergency Runbook §2 |
| "Update did not complete" | Emergency Runbook §3 |
| Need to update manually | Emergency Runbook §4 |
| Need to roll back a bad update | Emergency Runbook §5 |
| No internet at the venue | Emergency Runbook §6 |
| Sync chip is red or yellow | Emergency Runbook §7 |
| Rows missing from the shared sheet | Emergency Runbook §8 |
| App is slow or hanging | Emergency Runbook §9 |
| Voided the wrong transaction | Emergency Runbook §10 |
| Gave reward tokens, then order was voided | Emergency Runbook §11 |
| Customer wants to change a confirmed payment | Emergency Runbook §12 |
| Hard block on Payment / "row mismatch" / cap-bound math won't balance | Emergency Runbook §12b — split into separate orders, one payment method per order |
| Sending diagnostic info without internet | Emergency Runbook §13 |
| Data appears gone or corrupted | Emergency Runbook §14 |

### Most-asked questions

#### The app will not open

Most common cause is the single-instance lock holding from a previous crash. **Ctrl + Shift + Esc → Task Manager → end "FAM Manager.exe" → relaunch.** If still stuck, see Emergency Runbook §2 for the lock-file delete steps.

If the app never opens (no window, no error), make sure you extracted the zip file before running — running `.exe` directly from inside a zip doesn't work.

#### Windows SmartScreen warning

On first launch, Windows may show a SmartScreen warning. Click **"More info"** then **"Run anyway."** This is normal for applications not distributed through the Microsoft Store.

#### "No active market day" messages

Open a market day first: **Market** sidebar item → select location → enter your name → **Open Market Day**.

#### Payment does not balance

If the Remaining card shows a number other than $0.00, click the **⚡ Auto-Distribute** button on the Payment screen. It balances method amounts to match the receipt total. Manual entry also works — just adjust amounts until Remaining is $0.00.

**v2.0.7+ note**: Auto-Distribute now respects per-row **⚡ Locked** (grey) state — it only fills rows whose ⚡ icon is **green** (Active). If clicking Auto-Distribute "does nothing," check the row's ⚡ icon: a grey one means the volunteer (or auto-detection from typing) locked the value. Click the grey ⚡ to release it back to Active, or add a new payment-method row to absorb the remainder.

#### Hard block on the Payment screen — math doesn't reconcile

If the Payment screen refuses to confirm — a "Payment row mismatch" warning, a per-vendor over- or under-allocation error, or an explicit "split this customer's receipts into two separate customer orders" recommendation — and one or two clicks of **⚡ Auto-Distribute** does not fix it, **the simplest, safest resolution is to break the customer's receipts into separate orders, one payment method per order.**

This is always the right answer when the math doesn't balance and nothing else fits. The cleanest sequence:

1. **Cancel** out of the Payment screen and **Discard** the in-progress order from Receipt Intake (or Pending Orders).
2. Create a new order for the **same customer label** (returning-customer dropdown).
3. Add only the receipts that one payment method will cover. Confirm.
4. Repeat for the next payment method on the remaining receipts.

Because the customer label is the same, the daily match cap accounting carries through automatically and reports still group by customer label. Nothing is lost. See the in-app help article `split-orders-when-stuck` and the troubleshooting flow `ts-payment-screen-hard-block` for the step-by-step.

#### "Stale market day was auto-closed" at startup

Normal safety feature. A market day was left Open with a date earlier than today; the app closed it automatically so today's transactions don't get attributed to a past day. The closed day's transactions are intact. Just open a new market day for today.

#### Something went wrong (generic "Unexpected Error" dialog)

The error details are saved to `fam_manager.log` in your data folder. The fastest way to share is **Help → System Status → Copy Diagnostic Info** — that includes the last 30 lines of the log automatically. Paste into an email to your coordinator.

#### "Network unavailable" in the sync error tooltip

The laptop can't reach Google. Check your Wi-Fi. Once back online, the sync resumes automatically. As of v2.0, repeated network errors are coalesced into a single warning per cycle — your log won't fill up with stack traces during an outage.

#### Multiple laptops at the same market

Fully supported. Each laptop needs the same `market_code` and a different `device_tag`. See the in-app help article `multi-laptop-deployment` or the Coordinator Handbook for setup steps.

---

## Common operator scenarios

A grab-bag of "I just want to know how to..." answers, written for the volunteer at the booth.

### "The customer wants to add another receipt after I confirmed the payment"

That's the **returning customer** flow. Sidebar → Receipt Intake → enter the customer's existing label (e.g., C-005) instead of creating a new one. The new receipt joins the existing day's record for that customer. They'll go through Payment again for just the new receipt.

### "The customer wants to change a payment method after I confirmed"

Use **Adjustments**, not Void. Sidebar → Adjustments → search by customer label / vendor / receipt total → Edit → modify payment lines → ⚡ Auto-Distribute → Save. The original confirmation is preserved in the audit log.

### "I voided the wrong transaction"

You can't un-void in the same session. Re-enter the transaction from scratch (Receipt Intake → Payment → Confirm). The void stays in the audit log as a correction; the new entry is fully valid.

### "I gave the customer reward tokens but the order was voided"

The Generated Rewards row stays as a historical record by design — pretending tokens went back hides a real inventory shortage. Write a sticky note: customer label, tokens given, void reason. Hand to the coordinator at end-of-day.

### "I'm not seeing my data on the shared Google Sheet"

Three checks:

1. The sync indicator chip — green means it pushed; anything else means try Sync to Cloud
2. Right tab on the sheet (receipts → "Detailed Ledger", vendor totals → "Vendor Reimbursement", FMNP → "FMNP Entries", rewards → "Generated Rewards")
3. Filter the sheet by your `market_code` and `device_id` (Help → System Status to find the device_id)

In-app: Help → Browse → search "data not on sheet" for the full troubleshooting flow.

### "We have no internet at the market"

Keep working. Everything works offline except sync itself. Auto-sync resumes when internet returns; or take the laptop home, click Sync to Cloud manually.

### "I need to send a diagnostic but I have no signal"

Help → System Status → Copy Diagnostic Info → Notepad → save to Desktop. Email it (or copy to USB) when you have signal.

### "My laptop died mid-market — what about today's data?"

Two layers of recovery:

1. The plain-text **ledger backup** at `%APPDATA%\FAM Market Manager\fam_ledger_backup.txt` records every confirmed transaction in plain English. Open in Notepad on a different laptop.
2. Auto-saved database backups in `%APPDATA%\FAM Market Manager\backups\` snapshot every 5 minutes during market days.

If the laptop's hard drive is fully dead, the shared Google Sheet (if you'd been syncing) has every row through the last successful sync. Pull from there. See `restore-from-backup` in the in-app help for step-by-step instructions.

### "I'm running a new laptop alongside an existing one — what do I need to know?"

The shared sheet handles multi-laptop merging automatically — each laptop has a unique `device_id` and a same `market_code`. Customer labels can repeat (C-005 on laptop A and C-005 on laptop B are different customers — that's fine; the device_id keeps them separate). Coordinator does the laptop setup; see the Coordinator Handbook for the checklist.

### "I keep seeing 'Sync skipped — network unavailable' in the log"

That's the v2.0 quiet-logging feature working as intended. Before v2.0, an internet outage produced ~6 stack traces per sync tick (one per sheet tab). Now you get one concise warning per cycle. Nothing is wrong — sync will resume automatically when internet returns.

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

### Transaction ID Format

Transaction IDs include the market code: **FAM-{CODE}-YYYYMMDD-NNNN**

Example: `FAM-BPFM-20260306-0005` means the 5th transaction at Bethel Park Farmers Market on March 6, 2026.

### Customer Label Format

Customer labels follow the pattern: **C-001-{TAG}**, **C-002-{TAG}**, etc., where `{TAG}` is your laptop's 3-character device tag (e.g. `C-001-A1B`).

The numeric portion (`001`, `002`, ...) resets at the start of each new market day.  The device tag is constant per laptop and shown in the header chip.

**Why the tag exists:** when multiple laptops run at one market, every device independently generates `C-001`, `C-002`, ...  The tag turns each into a globally unique label so coordinators can refer to "C-005-A1B" or "C-005-LB1" without ambiguity.

**Customizing the tag:** Settings → Preferences → Device Identity → Device Tag accepts 1-4 alphanumeric characters.  Set `LB1` for "Laptop 1", `MGR` for the manager's machine, etc.  Leave blank to use the auto-derived hash.

**Legacy labels:** customer labels from before v1.9.9 keep their old `C-NNN` format (no tag).  They remain valid; only newly-generated labels carry the tag.

### Backup Retention

The app keeps the **20 most recent** database backups. A typical market day produces ~3 backups (open, auto, close), so this retains approximately the last 6-7 market days of snapshots.

---

## Rewards Program (v2.0+)

The Rewards feature is a **customer-facing marketing/loyalty add-on**.  When a customer pays for an order using a configured source payment method (e.g. SNAP), the FAM rep hands them physical scrip tokens (e.g. JH Food Bucks) at confirmation time as a thank-you.

> ⚠️ **Important — not financial.** Rewards are NOT part of vendor reimbursement, NOT FAM match, and NOT linked to any payment line item.  The vendor does not see or redeem these tokens.  Reward amounts are NEVER stored against transactions — they are recomputed on demand from the rules + the order's customer-paid totals.

### Default rule

A fresh install seeds:

> For every **$5.00 of SNAP** customer-paid in a confirmed order → hand the customer **one $2.00 JH Food Bucks token**.

The rule is **active by default** in v2.0.  If your market does not run this loyalty program, disable it in Settings → Rewards (the rule is preserved for later re-enabling).

### Math (whole-increment, NOT pro-rated)

The customer earns one full reward unit per **whole** threshold crossed:

| SNAP customer-paid | Food Bucks tokens earned |
|---|---|
| $4.99 | 0 ($0.00) |
| $5.00 | 1 ($2.00) |
| $7.50 | 1 ($2.00) — only one full $5 was crossed |
| $10.00 | 2 ($4.00) |
| $14.99 | 2 ($4.00) |
| $15.00 | 3 ($6.00) |

Source totals are summed **per customer order** (across all of the order's vendor receipts).  Voided transactions don't contribute.

### Where you'll see rewards

1. **Payment confirmation dialog** — the new "🎁 GIVE TO CUSTOMER" zone appears below the regular collect zone, listing the scrip the rep should hand out.  Purple/violet styling so it can't be confused with a payment item.
2. **Printed receipt** — a "Rewards Earned" section near the bottom (between the financial summary and the thank-you footer).
3. **Reports → Generated Rewards** — a derived table that recomputes against current data on every refresh.  Voiding a payment automatically removes that order's contribution.
4. **Cloud sync** — uploads to a "Generated Rewards" sheet by default.

### Configuring rewards (Settings → Rewards)

* **Master toggle** — globally enable or disable the feature without losing your rules.
* **Add Rule** — pick the source method (SNAP, Cash, anything active), the threshold dollar amount, and the reward method (must be a denominated method like Food Bucks, Food RX, JH Tokens — SNAP/Cash/FMNP cannot be reward methods because the rep doesn't physically hand them out).
* **Multiple rules** — supported.  Each rule fires independently against its source method's order total.
* **Disable / Delete** — Disable preserves the config; Delete is permanent.

### When does a reward get recorded?

A reward row is **written exactly once** — at the moment a payment is confirmed.  After that, the row is part of the historical record and is **never modified**.  This means:

* **Pre-feature transactions don't appear retroactively.**  Turning the feature on (or adding a new rule) does NOT cause prior orders to suddenly show up in the Generated Rewards report — only orders confirmed *after* the rule was active will appear.
* **Disabling the feature later does NOT wipe history.**  The Generated Rewards report still shows everything that was already given.
* **Editing or deleting a rule does NOT change past rewards.**  The row keeps its snapshot of the rule that was active at the moment of confirmation.
* **Voids and adjustments do NOT change past rewards.**  The cashier already handed the tokens; the row stays as the historical record.  Operationally, any conversation with the customer about a returned reward happens outside the app.

This design is the same write-once posture as the printed customer receipt: a record of what actually happened at the time of payment, immune to later edits.

---

## Glossary

Plain-English definitions of every term that shows up in the app, the printed receipt, the Google Sheet, and these docs. The in-app `glossary` article has the same content searchable from Help → Browse.

### App and people

| Term | Meaning |
|---|---|
| **FAM** | Food Assistance Match. The subsidy program. When the app says "FAM match" it means the dollars FAM contributes on top of what the customer pays. |
| **FMNP** | Farmers' Market Nutrition Program. A state-funded voucher / check program separate from FAM. |
| **Vendor** | A farmer or seller at the market. |
| **Customer** | The shopper. Tracked by short label (e.g., C-005) across multiple receipts on the same day. |
| **Coordinator** | The person who runs the market or the FAM program. Configures Settings, troubleshoots, reconciles end-of-day. |
| **Volunteer** | The person at the booth running the app. |

### Identifiers

| Term | Meaning |
|---|---|
| **market_code** | The 4-letter code for a market location (e.g., `BPFM`). Set in Settings → Markets. Shows in the title bar in brackets. |
| **device_id** | A short tag identifying *this laptop*. Used on the shared Google Sheet to tell which laptop produced each row. Defaults to a 3-character auto-derived tag; customizable in Settings → Preferences. |
| **fam_transaction_id** | The unique ID for a transaction, e.g., `FAM-BPFM-20260501-0001`. Format: `FAM-<market_code>-<YYYYMMDD>-<NNNN>`. Adjustments search this. |

### Concepts

| Term | Meaning |
|---|---|
| **Composite key** | When the shared sheet matches a row by multiple columns at once (e.g., market_code + device_id + date + customer label), preventing two laptops from accidentally overwriting each other. |
| **Upsert** | "Update or insert." When syncing, the app updates a matching row if one exists, or inserts a new row if not. |
| **Audit log** | An append-only history of every change in the database (confirms, voids, adjustments). "Append-only" means rows are added but never modified or deleted, so it's a permanent record. |
| **Service account** | A Google identity used by the app to authenticate to Sheets and Drive. The credentials JSON file is the proof of identity. Coordinators handle this; volunteers just receive the file once. |
| **Drive folder ID** | The long string in a Google Drive folder URL. Where the app uploads photos. |
| **Spreadsheet ID** | The long string in a Google Sheets URL. Identifies the shared workbook the app syncs to. |
| **Soft delete** | The row stays in the database but is hidden from normal views. Used when something is "deleted" but still needs to be referenced for history. |
| **Schema migration** | A database upgrade that runs automatically when the app launches a newer version against an older database file. Always preceded by a `.bak` snapshot. |

### Money math

| Term | Meaning |
|---|---|
| **Match cap** | The maximum FAM dollars a single market day can spend. Configured per market. Once hit, new orders show "Cap reached" and FAM contributes 0. |
| **Match percent** | For each payment method, the percentage FAM matches. SNAP at 100% means $1 SNAP earns $1 FAM. FMNP at 50% means $5 FMNP earns $2.50 FAM. |
| **Penny reconciliation** | Rounding cents so the totals on a multi-method payment add up exactly to the receipt total. Automatic. |
| **Drift / drift cent** | The 1¢ rounding leftover that the app distributes between methods to keep totals exact. |
| **Denominated payment** | A payment whose amount can only be a multiple of a fixed denomination (e.g., Food Bucks in $5 increments). The app prevents non-multiples. |
| **Forfeit** | When a denominated payment overshoots the receipt (customer hands $15 of FMNP for an $11 receipt). Vendor gets full $11; customer "forfeits" the unmatched $4 of physical paper. |
| **Unallocated funds** | Money that was on a confirmed transaction but isn't covered by any line after an adjustment. Shows in the audit log as `UNALLOCATED_FUNDS`. |

### Sync

| Term | Meaning |
|---|---|
| **Sync indicator chip** | The colored dot in the title bar showing the latest sync state (green/yellow/red/gray). |
| **5-minute auto-sync** | The timer that triggers sync automatically while a market day is open. |
| **60-write/min quota** | Google's rate limit on Sheets writes. The app paces itself to stay under it. |
| **Offline-quiet logging** (v2.0) | When sync fails because of no internet, the log gets ONE warning per cycle instead of a full traceback per sheet tab. |

### Files and folders

| Term | Meaning |
|---|---|
| **Data folder** | `%APPDATA%\FAM Market Manager\` — where everything lives. |
| **fam_data.db** | The main SQLite database file. Source of truth. |
| **WAL** | Write-Ahead Log, a file SQLite uses while committing. You'll see `fam_data.db-wal` and `fam_data.db-shm` next to the main file. Don't move or delete them while the app is running. |
| **Backup** | A `.db` file copied to `backups/` at fixed intervals during a market day. |
| **Ledger backup** | `fam_ledger_backup.txt`, a plain-text human-readable copy of every confirmed transaction. |
| **Pending-update marker** | `_pending_update.json`. A short note left behind by the updater so the new version can verify it actually installed. |
| **Instance lock** | `.fam_instance.lock`. Prevents two copies of the app from running against the same data folder. |
| **Update backup** | `_update_backup\` — previous version's binaries, kept for rollback. |

### Status words

| Term | Meaning |
|---|---|
| **Confirmed** | The operator clicked Confirm; FAM has committed match dollars. |
| **Voided** | The transaction was cancelled. Match dollars are released; the customer didn't pay. |
| **Adjusted** | A confirmed transaction was edited later. The audit log records what changed. |

---

## See also

- **`EMERGENCY_RUNBOOK.md`** — symptom-indexed recovery steps for market-day disasters
- **`COORDINATOR_HANDBOOK.md`** — setup, deployment, monthly reconciliation, escalation
- **`QUICK_REFERENCE.md`** — printable single-page cheat sheet
- **In-app Help** (sidebar) — articles, troubleshooting, and live diagnostic
- **`README.md`** — developer-facing repo overview
- **GitHub repository** — `https://github.com/seansaball/fam-market-manager`

