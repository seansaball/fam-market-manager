# FAM Market Manager — Coordinator Handbook

> **For the person responsible for keeping the system running** —
> typically one or two operations leads at the umbrella organization.
> If you've inherited responsibility from the project owner, this is
> what you need to know.
>
> Last updated for v2.1.2 — July 2026.

---

## Your role

You are the bridge between the volunteers at the booth and the
technical infrastructure. Volunteers handle market-day workflows.
The system handles money math, reports, and cloud sync. **You
handle:**

1. **Setup of new laptops** before they go to a market
2. **Credentials & access** for Google Sheets and Drive
3. **Multi-laptop deployments** (one market with two or more devices)
4. **Updates** rolled out to all laptops
5. **Reconciliation issues** at end-of-day or month
6. **Triage of escalations** from volunteers when the runbook isn't enough

You don't need to write code. You need to know where things live,
how to read a diagnostic, and when to escalate to the project owner.

---

## What every volunteer should have

Before market day:

- [ ] A working laptop with FAM Manager installed
- [ ] A printed copy of `EMERGENCY_RUNBOOK.md` in the laptop case
- [ ] The market_code preset in Settings → Markets (e.g., `BPFM`)
- [ ] A unique device tag in Settings → Preferences (e.g., `LB1`)
- [ ] The Google credentials file already loaded
- [ ] The Spreadsheet ID configured
- [ ] At least one successful test sync after install

If any of these are missing, do them BEFORE the laptop leaves your
hands.

---

## The first-time deployment of a new laptop

### Step 1: Install the app

Use the latest release zip from
`https://github.com/seansaball/fam-market-manager/releases`.

1. Download `FAM_Manager_vX.Y.Z.zip`
2. Right-click → Extract All
3. The extracted folder contains everything; copy it to `C:\Program Files\FAM Manager\` (or any folder)
4. Right-click `FAM Manager.exe` → Send to → Desktop (create shortcut)
5. Launch — Windows SmartScreen will warn on first run; click "More info" → "Run anyway"

> **Pre-flight check for image-cloned laptops (v2.0.6+).** If your fleet was deployed by cloning one Windows image across multiple laptops, verify each device has its own MachineGuid registry value before launching the app for the first time. Cloned images can share the same MachineGuid, which v2.0.6+ refuses to launch with — it would silently corrupt cross-device cloud sync (every device's rows would collide on the same composite key).
>
> In an elevated PowerShell on each laptop:
> ```powershell
> (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' MachineGuid).MachineGuid
> ```
> You should see a unique GUID per device. If a value is missing, blank, or identical across two laptops, generate a fresh one before launching:
> ```powershell
> $g = [guid]::NewGuid().ToString()
> Set-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' MachineGuid $g
> ```
> If a device hits this on first launch, you'll see a "Device Identity Required" dialog with the same instructions — no data is touched, and the device runs normally once the registry value is set.

### Step 2: Initial settings

On first launch, the tutorial overlay walks through the basics. At
the end, choose **"Yes — Load Default Data"** to seed the app, or
**"No Thanks — Start Blank"** if you have a `.fam` settings file.

### Step 3: Configure identity

Settings → Markets:
- Make sure your market is in the list with the correct `market_code` (4-letter code)
- Or add a new one if needed

Settings → Preferences → Device Identity:
- Set a 1–4 character **Device Tag** unique to this laptop
- Suggestion: `LB1`, `LB2` for "Laptop 1", "Laptop 2"
- Or `BP1`, `BV2` to combine market and laptop number

### Step 4: Cloud sync

Settings → Cloud Sync:
1. Click **Load Credentials** → pick the Google service-account JSON file
2. Paste the **Spreadsheet ID** (from the URL of the shared sheet — between `/d/` and `/edit`)
3. Click **Save Sync Settings**
4. Click **Sync to Cloud** to test
5. Verify a row appears on the shared sheet

If the test sync fails with a permission error, the service account
email isn't shared on the sheet yet. See **Adding the service
account to the sheet** below.

### Step 5: Updates configuration

Settings → Updates:
- The repository URL is pre-filled with the official one
- Auto-check is on by default — leave it on
- Click **Check for Updates** once to verify the connection works

### Step 6: Verification checklist

Before the laptop goes to a market:

- [ ] Open and close a test market day successfully
- [ ] Enter a fake transaction; confirm it; void it
- [ ] Sync to Cloud — chip turns green, row appears on the shared sheet
- [ ] Help → System Status — verify everything is populated
- [ ] Click **Copy Diagnostic Info**, paste somewhere, sanity-check the values

---

## Google Sheets / Drive setup

### What the system uses

The app uses a Google **service account** — a non-human Google identity
that authenticates with a JSON file instead of a password. The
service account has its own email address (looks like
`fam-sync@your-project.iam.gserviceaccount.com`).

### Adding the service account to the sheet

1. Open the shared Google Sheet in a browser
2. Click **Share** (top right)
3. Paste the service account email
4. Set permission to **Editor**
5. **Uncheck** "Notify people" (the service account has no inbox)
6. Click **Share**

The same identity handles Drive automatically — when the app needs
to upload a photo, it'll create a folder if one doesn't exist.

### Generating a new credentials file

If you need to rotate or replace the credentials:

1. Open Google Cloud Console: https://console.cloud.google.com
2. Select the project that owns the existing service account
3. **IAM & Admin** → **Service Accounts**
4. Click the existing service account (or create a new one)
5. **Keys** tab → **Add Key** → **Create new key** → **JSON**
6. The browser downloads a `.json` file
7. Distribute this file to each laptop (USB stick, secure email, etc.)
8. On each laptop: Settings → Cloud Sync → Load Credentials → pick the new file → Save Sync Settings

The old credentials remain valid until you revoke them in Google
Cloud Console. Coordinate the rollover so no laptop is using a
revoked key.

---

## Multi-laptop deployments

When two or more laptops cover the same market:

### What works automatically

- Each laptop syncs independently to the same shared sheet
- Rows are tagged with `market_code` + `device_id`, so they don't
  overwrite each other
- Customer labels can repeat across laptops (C-005 on laptop A and
  C-005 on laptop B are different customers)
- Reports on the shared sheet merge everything; filter by `device_id`
  to see one laptop's contributions in isolation

### What you must set up

- Different `device_tag` on each laptop (Settings → Preferences)
- Same `market_code` for the same market
- Same credentials file
- Same Spreadsheet ID

### What NOT to do

- **Don't copy `fam_data.db` between laptops.** That clones identity
  and breaks the merge.
- **Don't change device_tag mid-day.** The shared sheet will see two
  "different" devices and rows will appear duplicated.
- **Don't run two copies on one laptop.** The instance lock prevents
  this; if it gets bypassed, the database can corrupt.

### End-of-day across laptops

Each laptop runs its own end-of-day. The shared sheet merges
everything. Pull totals from the sheet, not from individual laptops.

---

## Updates

### Recommended cadence

- **Test new releases on one laptop first.** Run a fake market day
  end-to-end before deploying.
- **Roll out between markets, not during.** A bad update during a
  market day is a recoverable but stressful event.
- Subscribe to GitHub release notifications so you know when new
  versions ship.

### How auto-update works

The app checks GitHub Releases once per day on launch. If there's a
new version:

1. Volunteers see a banner offering to update
2. If they click "Download & Install," the app:
   - Downloads the release zip
   - Verifies the file size
   - Writes a `_pending_update.json` marker
   - Quits and runs an installer batch script
   - The script copies new files over the old, then relaunches
3. The relaunched app reads the marker and confirms the version
4. If the version doesn't match, a "did not complete" dialog fires

### Disabling auto-update

If you'd rather control updates manually:

Settings → Updates → uncheck **"Auto-check for updates on launch"**.
Update each laptop manually using the steps in the Emergency Runbook.

### Pinning all laptops to the same version

You can do this with config management or simply by visiting each
laptop and not clicking "Download & Install" until you're ready.
There's no central control plane — but for ~5–20 laptops, manual
sequencing is fine.

### Rolling back

The app keeps the previous version's binaries at
`%APPDATA%\FAM Market Manager\_update_backup\`. If a new version is
broken:

1. Quit the app
2. Copy everything from `_update_backup` over the install folder
3. Launch — you're back on the previous version

The data folder is never touched, so you don't lose any market data
in the rollback.

---

## Reading a diagnostic

When a volunteer sends you their `Copy Diagnostic Info` paste, here's
what to look at:

```
App version       : 2.0.0         ← which version are they on?
Data directory    : C:\Users\...   ← any chance multiple users are conflicting?
Market code       : BPFM           ← matches their assignment?
Device ID         : LB1-abc123     ← is this their tag?
Open market day   : Bethel Park...
Instance lock     : held by pid... ← if "STALE", direct them to delete it
```

```
-- Sync ----
Last sync         : 2026-05-01... ← how recent? '(never)' is a red flag
Last sync error   : Network unav.. ← any error here is the smoking gun
Sheet configured  : yes            ← if 'no', the spreadsheet ID isn't saved
Credentials loaded: yes            ← if 'no', they didn't load the JSON
```

```
-- Updates ----
Last update check : ...
Update source     : github.com/... ← non-default URL is a misconfiguration
Pending update    : YES — target was 2.0.0   ← stuck mid-update
```

```
-- Rewards ----
Rewards           : enabled, 2 rules, 47 today
                                   ← matches their market's expectations?
```

```
-- Records ----
Confirmed txns    : 12             ← do these counts make sense?
Voided txns       : 0
Active FMNP rows  : 3
Market days total : 87
Audit log rows    : 2,401
```

```
-- Disk usage ----
Database          : 2.4 MB         ← anything over 100 MB is unusual
Backups folder    : 18.7 MB (24 files)  ← 0 files = backups disabled?
Oldest backup     : fam_2026-04-01... ← how far back can they recover?
```

```
-- Log tail ----
[last 30 lines of fam_manager.log]
                                   ← raw error context, look for traceback
```

### Common diagnostic patterns

| What you see | What it means |
|---|---|
| `Last sync error: Network unavailable` | A blip — usually clears on its own. If repeated for hours, the laptop's Wi-Fi or DNS is broken. |
| `Last sync: (never)` + credentials loaded | Spreadsheet ID is wrong, or service account isn't shared on the sheet. |
| `Pending update: YES` | The user has been stuck in a half-updated state. Walk them through manual update or rollback. |
| `Instance lock: STALE` | A previous crash left the lock file. Delete it (Section 2 in Emergency Runbook). |
| Database > 500 MB | Something is wrong — investigate. Most installs are under 50 MB. |
| Backups folder is 0 bytes / 0 files | Backup mechanism not running. The app should be auto-creating backups during market days; this is a real problem. |
| Audit log rows growing past 1M | Old data should be archived. Not urgent but worth flagging. |

### When to escalate

If a volunteer reports any of the following, escalate to the project
owner (or the technical contact):

- The app crashes on launch and Section 2 of the runbook doesn't fix it
- A confirmed transaction's totals don't match the receipt photo
- Reports show negative match dollars
- The audit log has gaps (missing entries between known events)
- The shared sheet shows rows from a `device_id` you don't recognize
- Anything involving money math being wrong

For everything else, follow this handbook + the Emergency Runbook
and the volunteer's diagnostic info.

---

## End-of-market external payments collection (v2.1.0+)

At the end of each market day, vendors hand the market manager the
physical scrip they accepted directly at their booths — FMNP checks,
Food RX vouchers, Food Bucks tokens. The workflow:

1. **Collect the scrip per vendor.** Keep each vendor's pile
   separate — entries are per (vendor, method).
2. **Count it per method.** Within a vendor's pile, sort by payment
   method and total each method's face value.
3. **Enter one entry per method** on the **External Payments**
   screen: pick the market day (a closed day is fine — this is the
   normal end-of-day flow), the method, the vendor, and the face
   total. The live preview and the save confirmation both name the
   exact amount FAM will owe the vendor.

The money math per entry: **FAM owes vendor = match on the face
value, plus the face value itself unless the vendor cashes the
original with the issuing program.** For a $10 handful of scrip:
FMNP (100% match, vendor cashes the check) → **$10, the match**;
Food RX (100% match, FAM collects the paper) → **$20, face + match**;
Food Bucks (0% match, FAM collects) → **$10, face only**. The match %
and denomination come from the method's existing settings — the only
external-specific settings are the two toggles in Settings → Payment
Methods → Edit ("Accept external matching", "Vendor cashes the
original instrument").

**Correcting a mistake: void + re-enter.** Entries snapshot the
method's configuration at save time, and the snapshots are immutable
— a settings change never re-values recorded entries, and an Edit
can change only the face value. If an entry was made under the wrong
settings (or against the wrong vendor/method), **delete (void) the
wrong entry, fix Settings if needed, and re-enter**. Both the voided
row and the replacement stay visible (the External Payment Entries
tab flags voided rows), so the audit trail is complete. The
**Reimbursement Basis** column shows which configuration each entry
was valued under.

**Food Trust mailing / reimbursement bookkeeping.** For methods
where FAM collects the paper (Food RX, Food Bucks — "Vendor cashes
the original" OFF), FAM mails the collected instruments to the Food
Trust, which reimburses **face value**. To know how much paper to
mail and how much reimbursement to expect, pivot the **External
Payment Entries** sheet tab by method and sum the **face value**
column (not FAM Owes Vendor — that includes the match, which is
FAM's own cost). The Reimbursement Basis column confirms which
entries are face-collected.

**Reward-type scrip is 0% match (ENH-001).** Scrip that customers
*earned* as a reward (JH Food Bucks) must be configured at **0%
match** — the match was already applied when the scrip was earned;
matching it again at redemption would double-pay. Fresh installs now
seed JH Food Bucks at 0%. **Existing markets should verify Settings
→ Payment Methods → JH Food Bucks** shows 0% before recording
external Food Bucks entries.

**If RX/Bucks are ever accepted at the FAM booth in a future
season:** enabling that is just a Settings flip (activate the method
for the Payment screen), and the External Payments screen's
booth-activity review prompt is the guard against the same physical
token being reimbursed twice.

---

## End-of-market EBT settlement (v2.1.0+)

The EBT terminal prints **one paper receipt per customer swipe**,
but a customer's order can span several vendors — so the Detailed
Ledger splits a single swipe across several rows. The **SNAP
Settlement** tab (Reports) folds them back: one row per customer
order with the order-level SNAP total — the exact figure on the
paper receipt — plus how many vendor receipts the order spanned.

Suggested routine:

1. After close, collect the terminal's receipt stack (or batch
   report).
2. Reports → pick the day in the **Market Day** dropdown (it
   narrows every report to exactly that day and resets the date
   range — the two are last-touched-wins alternatives) →
   **SNAP Settlement** tab. The Vendor and Payment Type filters
   are ignored on this tab on purpose — a vendor sub-total would
   never match the paper.
3. Tick the **Verified** checkbox on each row as you match its
   paper receipt — both run in time order, and verified rows turn
   green so the remaining work stands out. Ticks are saved on this
   laptop (they survive restarts), so the exercise can be paused
   and resumed. Verification is a working note, not a financial
   record — it never syncs and never changes any report total.
4. **Paper receipt with no row** = a swipe that was never confirmed
   in the app. Resolve same-day if possible (see the *SNAP
   Settlement* in-app help article for recovery options).
5. **Row with no paper receipt** = confirmed in the app but never
   run on the terminal. Charge the card if the customer is still
   present; otherwise void or adjust the transaction so the books
   match what was actually collected.
6. Export CSV if you keep daily settlement files.

This report is **local to each laptop** (no Google Sheets tab — by
design; every dollar in it already syncs via Detailed Ledger). On
multi-laptop markets, run the settlement on each laptop: a laptop
only shows the orders it processed, which usually matches "one
terminal per table" setups one-to-one.

---

## End-of-market vendor verification (v2.1.0+)

The companion to the EBT settlement: confirming each vendor's
receipt total with them in person before they pack up. With the
day selected in **Market Day**, the **Vendor Reimbursement** tab
shows a **Verified** checkbox per vendor:

1. Same starting point — Reports → pick the day in **Market Day**.
2. Walk the vendors; for each one, read them their row's totals
   and tick **Verified** when you agree. The row turns green so
   the remaining vendors stand out.
3. Ticks are saved per **vendor per market day** on this laptop —
   pause the walk, come back, they're still there. A date-range or
   month view has its **own independent checkbox** per vendor:
   ticking "June 2026" records that you reconciled the vendor's
   June total, without touching the per-day ticks inside it (and
   unchecking the month never unchecks the days). The date popup
   has a **whole-month quick pick** for the month-end
   check-cutting pass. (With no time scope at all — All Dates +
   All Market Days — the column shows an inert dash: there is no
   scope to attach a mark to.)

**The marks are purely a place-keeping aid.** Checked or
unchecked, they have zero effect on what FAM reimburses anyone —
they never sync, never hit the audit log, and no money math reads
them. If a vendor disputes a number, that conversation goes
through the normal adjustment workflow; the checkbox just tracks
that the conversation happened.

---

## Monthly reconciliation

### Pull these reports

From the shared Google Sheet (best — merged across all laptops):

1. **Vendor Reimbursement** — v2.0.9+ emits **one row per vendor per calendar month** (separate columns: human-readable **Month** like "April 2026" and sortable **Year-Month** like "2026-04"). Sort or filter on Year-Month to compare month-over-month per vendor. v2.1.0+ adds a **"<Method> (External)"** column per external method (e.g. "Food RX (External)") next to the existing **FMNP (External)** column — all of them add into **Total Due to Vendor**. On a spreadsheet that pre-dates v2.1.0, new columns physically append at the **end** of the header row; reordering them by hand is safe because the app writes by header name, not position.
2. **FAM Match Report** filtered by month → total match dollars by payment method, including **"<Method> (External)"** rows for external entries
3. **External Payment Entries** (v2.1.0+) filtered by month → the per-entry audit layer for ALL external scrip — FMNP checks, Food RX, Food Bucks: face value, config snapshots, derived **FAM Owes Vendor**, **Reimbursement Basis**, with voided rows flagged. This is the tab to pivot when reconciling external payouts entry-by-entry or computing Food Trust mailing totals (see the collection workflow above).
4. **FMNP Entries** — DEPRECATED as of v2.1.0. During the transition it only carries markets that haven't upgraded yet: an upgraded market's full FMNP history moves to External Payment Entries automatically, and its rows are removed from this old tab on the next full sync. Until every market is upgraded, read FMNP from BOTH tabs (each market's records live on exactly one of them — never sum a market across both). Once the tab is empty, delete it.
5. **Generated Rewards** filtered by month → tokens given to customers (if your market does rewards)

### Reconcile against

- Physical vendor payment records (your bookkeeping)
- FMNP check inventory before/after
- Food RX / Food Bucks scrip collected and mailed to the Food Trust
  (face totals pivoted from the External Payment Entries tab)
- Reward token inventory before/after
- Bank deposits / cash counts

### Common discrepancies

| Symptom | Likely cause |
|---|---|
| Vendor total too low | A laptop didn't sync — check Last sync timestamps |
| FMNP count off by exact amount of one entry | A FMNP entry was voided after coordinator reviewed |
| Reward inventory short by a few tokens | A voided order with rewards — reward row stays as historical record by design |
| Match dollars exceed cap | Cap was raised mid-day. Check Settings → Markets audit log |
| An external entry's payout looks wrong | Check its **Reimbursement Basis** on the External Payment Entries tab — it names the config snapshot the entry was valued under. Settings changes never re-value recorded entries; the correction is void + re-enter under the fixed settings |

The audit log (Reports → Activity Log) records every change with
timestamp and operator. Use it as the authoritative history when
two records disagree.

---

## End-of-month checklist

- [ ] All laptops have synced their final market days
- [ ] Pull the four reports from the shared sheet
- [ ] Reconcile against physical inventory and bank records
- [ ] Archive the previous month's photos from Drive (move out of the active folder if needed)
- [ ] Review any voided orders for patterns (training opportunity?)
- [ ] Review any "Pending update" markers — should be cleared after a clean update
- [ ] Apply any pending app updates to the staged laptop, test, then roll out
- [ ] Confirm backup volumes — every laptop should have backup files in their `backups/` folder

---

## Decommissioning a laptop

When a laptop is being retired:

1. Sync one final time — confirm green chip
2. Make a copy of the entire `%APPDATA%\FAM Market Manager\` folder onto a USB stick
3. Verify the data exists on the shared Google Sheet
4. The laptop can now be wiped — the data is preserved in the sheet and the USB backup
5. Remove the laptop's `device_id` from any documentation; assign it to a new device only after wiping

---

## Onboarding a new volunteer

Day-of:

1. Show them the laptop case with the printed Emergency Runbook
2. Show them the sidebar — what each section does (Market, Receipt Intake, Payment, Adjustments, External Payments, Reports, Settings, Help)
3. Walk through opening a market day, entering one fake transaction, confirming it, voiding it
4. Show them the sync indicator and what colors mean
5. Show them Help → Browse and how to search for a topic
6. Show them Help → System Status → "Copy Diagnostic Info" so they know how to send you info if something goes wrong
7. **Tell them: when in doubt, look at the printed runbook first, then send a diagnostic.**

---

## Common training points

- **The app saves before it syncs.** A red sync chip never means data
  is at risk locally.
- **Voids are permanent in the same session.** If you void wrongly,
  re-enter the transaction.
- **Adjustments edit a confirmed transaction.** Use this for "wrong
  payment method" situations.
- **The customer label is a tag, not a name.** It's how the app links
  multiple receipts from one customer in the same day.
- **Match math is automatic.** Don't try to override it — if the math
  looks wrong, something else is wrong (cap reached? FMNP not active?).
- **Per-row ⚡ toggle on Payment rows (v2.0.7+).** Each non-denom
  payment row has a small ⚡ icon. **Green** = Auto-Distribute will
  fill it; **grey** = Locked at the volunteer's typed value. Typing
  into the amount field auto-locks the row. Only one row can be Active
  (green) at a time — adding a third payment method defaults the new
  row to Locked. If a volunteer says "Auto-Distribute did nothing,"
  check whether the row they expected to fill is grey — they need to
  click ⚡ to release it, or add another row to absorb the remainder.
- **External Payments Entry "All Market Days" filter (v2.0.7+).** The
  market-day dropdown defaults to "All Market Days" for browsing the
  full entry history. The Add Entry button greys out (with a visible
  inline hint) because new entries need a specific market day.
  Volunteers pick a date from the dropdown to enable it.
- **External Payments Entry money is snapshot-valued (v2.1.0+).**
  Every entry snapshots the method's match % / cashes-original /
  denomination at save time; the FAM-owed payout is always derived
  from the entry's own snapshots, never from current Settings. If a
  volunteer asks why two entries for the same method show different
  payouts, the Reimbursement Basis column is the answer.
- **Customer Forfeit (v2.0.7+).** When a customer hands a $10 token
  for a $1.45 receipt, the $8.55 over-tender shows in the Customer
  Forfeit summary card and report column. Vendor still gets the full
  receipt total ($1.45); the forfeit is just unaccounted token value
  recorded for the audit trail.

---

## Project-owner contact information

> **Fill this in for your organization** before printing this
> handbook:

- **Project owner name:**
- **Email:**
- **Phone:**
- **Best hours to reach:**
- **GitHub repo:** https://github.com/seansaball/fam-market-manager
- **Issue tracker:** https://github.com/seansaball/fam-market-manager/issues

---

## When to escalate (recap)

| Situation | Action |
|---|---|
| Volunteer needs help during market day | Refer to printed Emergency Runbook |
| Volunteer needs help between markets | Use this handbook + their diagnostic info |
| Money math looks wrong | Pull the audit log first; if it confirms the discrepancy, escalate to project owner |
| Database file appears corrupt | Make a safety copy → try restore-from-backup steps → if that fails, escalate |
| Multiple laptops report the same issue at the same time | Likely a Google API outage; check status.cloud.google.com — wait 30 min before escalating |
| Anything you've never seen before | Send the volunteer's diagnostic + your guess to the project owner |

---

## Versioning notes

This handbook is for **v2.1.2**. Major changes recently:

- **v2.1.2**: **"Correct Amount Collected"** (ENH-008) — a new choice when you click **Adjust** (you're asked "Adjust Payment" vs "Correct Amount Collected"). Use it when the amount actually charged — usually on the EBT terminal — differs from what was logged (logged $12.75, card charged $12.50). It lowers the recorded amount to the real charge (so SNAP totals match the bank deposit), **keeps the FAM match unchanged**, and books the difference as **Unallocated Funds**; the vendor total never changes. Non-denominated methods only (SNAP/Cash); under-collection only. Note: because SNAP is doubled, a $0.25 short-charge shows as $0.50 absorbed — the SNAP line is the one that matters for the bank. **ACH Enabled column** on Vendor Reimbursement (in-app + Sheets): Yes/No per vendor, set in Settings → Vendors → edit vendor; fills in on the next sync including past months. **Auto-sync keeps running after the market day closes** (Settings → Cloud Sync, "sync every 5 minutes") — data entered later, e.g. reconciling the next day online, flushes automatically with no manual sync. **Quieter offline logs** — the app records "offline" once per outage instead of every few minutes. Includes v2.1.1 (sub-dollar denominations — set $0.50 JH Tokens etc. in Settings → Payment Methods → Denomination). No schema migration (v41).
- **v2.1.0**: **External Payments Entry** (ENH-002). The FMNP Entry screen is rebranded "External Payments Entry" (sidebar: "External Payments") and generalized to record physical scrip for **any** payment method with "Accept external matching" ON (Settings → Payment Methods → Edit); FMNP is enabled by default and stays the dropdown default. FAM owes vendor = match on the face value + the face value itself unless "Vendor cashes the original instrument" is ON ($10 worked examples: FMNP 100%/cashes → $10 the match; Food RX 100%/collected → $20 face+match; Food Bucks 0%/collected → $10 face only). Zero new money fields — match % and denomination are inherited from the method's existing settings. Entries snapshot the config at save; settings changes never re-value history; corrections are void + re-enter. Guards: live payout preview, save confirmation naming the FAM-owed amount, denomination whole-multiple validation, large-amount warning, booth double-count review prompt. Sheets: Vendor Reimbursement gains "<Method> (External)" columns adding into Total Due (FMNP (External) unchanged forever; on existing spreadsheets new columns append at the END of the header row — manual reorder is safe, writes are by header name); new required **External Payment Entries** audit tab; FAM Match Report "<Method> (External)" rows; Detailed Ledger EXT- rows; ledger backup external section + grand total. Schema v37 → **v38**; app versions ≤v2.0.9 refuse to open the upgraded database until they update — mixed fleets need no coordinated upgrade (each laptop's sheet rows are device-scoped); upgrade pace only gates which markets can USE the feature. Also ships **ENH-001**: JH Food Bucks now seeds **0% match** (reward-type scrip is never matched again — the match was applied when the scrip was earned); existing markets verify Settings → Payment Methods → JH Food Bucks.
- **v2.0.9**: Vendor Reimbursement shared-sheet rows now split by calendar month — one row per (market × vendor × month) instead of one ever-growing all-time cumulative row.  New **Month** column is human-readable ("April 2026") and a new **Year-Month** column carries the sortable form ("2026-04"). The math identity (Σ method-cols + FAM Match − Customer Forfeit + FMNP_External = Total Due) holds within each monthly row. Upgrade behavior: each laptop's old all-time row is **replaced automatically** on its first v2.0.9 sync (the stale-row cleanup removes it as the new monthly rows are written). On a multi-laptop fleet the old rows disappear one device at a time as each laptop upgrades — a mix of old and new rows during the rollout window is normal, not data loss. Manual deletion is only needed for rows belonging to a retired laptop that will never sync again (identifiable by a blank Year-Month cell). No schema migration; on-screen reports and other tabs unchanged.
- **v2.0.7**: Hotfix release covering post-v2.0.6 onsite findings.  Schema v34 → v35 (one forward migration that backfills SNAP and Cash for every vendor; idempotent).  **Universal SNAP / Cash binding policy** — both methods are now ticked, locked, and labelled "universal" in Settings → Vendors → Eligible Payment Methods; you cannot accidentally configure a vendor as SNAP-ineligible.  Other methods (Food Bucks, Food RX, FMNP) remain per-vendor configurable.  **Denomination preservation through adjustments** (engine snap-back + save-layer guard).  **Adjustment safety gate** for denominated transactions — Adjust on a txn that includes Food Bucks / Food RX / FMNP now opens a Void-Instead / Adjust-Anyway / Cancel dialog.  **Single-vendor multi-receipt allocation** corrected so split receipts at one vendor reconcile cleanly.  **Vendor Reimbursement after voids** — surviving receipts on a partially-voided multi-receipt order roll up correctly now.  **Photo dedup cache cleanup** on void / FMNP delete / FMNP photo replace (a re-attached photo no longer reads as a duplicate of its now-orphaned hash).  **Cap-bound split-order recommendation** — when a returning customer's daily FAM cap is too low to absorb a particular combination of denominated + non-denominated payments, the Payment screen no longer hard-blocks with a generic "row mismatch"; it surfaces a dialog naming the cap as the cause and recommending the volunteer break the customer's receipts into separate orders one method at a time.  Documented in the new in-app help article `split-orders-when-stuck` and troubleshooting flow `ts-payment-screen-hard-block`.  3,504 tests.
- **v2.0.6**: Production season release.  Per-vendor payment-method eligibility (Settings → Vendors), configurable rewards engine (Settings → Rewards), redesigned Payment Confirmation Dialog.  Multi-workstation cloud sync hardened end-to-end — settings changes propagate to the shared sheet, closed-day mutations sync correctly, Vendor Reimbursement cleanup is multi-market-aware, reset preserves other devices' rows, market renames protected against code-shift orphaning.  Photo dedup cache cleaned on void/delete/replace.  Schema v33 → v34 (additive).  3,387 tests.
- v2.0.1: Pre-deployment hardening pass — InstanceLock wired into app.py, narrow-scope auto-sync no longer deletes historical rows, FMNP face-value reporting, three-gate reset, defensive UF triggers
- **v2.0.0**: First production release.  Brings the comprehensive hardening + documentation pass: cross-platform single-instance lock, atomic photo writes, offline-quiet sync logging, customer rewards add-on, 5 hardening sessions (+277 new tests), 11 new in-app help articles, 6 new troubleshooting flows, and three new printable docs (Emergency Runbook, Coordinator Handbook, Quick Reference).  Skipped the v1.9.10 internal designation and went straight from v1.9.9 to v2.0 to signal the production milestone.
- v1.9.9: Composite-key sync, multi-device support, audited vendor/method CRUD
- v1.9.8: FMNP-as-payment improvements, Adjustments overhaul
- v1.9.7: Drive photo verification (tri-state)
- v1.9.6: TLS / certifi fix for frozen builds
- v1.9.5: Pending-update marker introduced
- v1.9.3: Auto-update zip layout fix

Older versions may behave slightly differently. If a volunteer is on
an older version, check the version chip in Help → System Status and
prioritize their update.

---

## Need to update this document?

The canonical copy lives at `docs/COORDINATOR_HANDBOOK.md` in the
GitHub repo. Open a pull request or email the project owner with
suggested changes.
