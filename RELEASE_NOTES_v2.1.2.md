# FAM Market Manager v2.1.2

**Previous email to markets:** v2.1.0 (June 2026)
**Release date:** July 2026
**Schema version:** 41 (no database migration — installs update in place)

This note rolls up everything since the v2.1.0 email (covering the
v2.1.1 and v2.1.2 updates). No action is required beyond taking the
update — a couple of items have optional setup, noted below.

---

## Updates since v2.1.0

### 1. Auto-sync now keeps running after the market day closes

**The problem:** markets that run on-site with no internet would enter
data, close the day, and reconnect later (often the next day) — but the
app had stopped auto-syncing the moment the day closed. Nothing reached
the cloud until someone manually clicked **Sync to Cloud**.

**Now:** the 5-minute auto-sync keeps probing after the market day
closes. As soon as the laptop is back online, any data entered while
offline flushes to the cloud automatically — no manual sync needed.

*Setup:* this uses the existing "sync every 5 minutes" option in
**Settings → Cloud Sync**. If a market wants the closed-day catch-up,
make sure that box is checked.

### 2. "Correct Amount Collected" — fix a terminal charge that didn't match

**The problem:** occasionally the amount actually charged on the EBT
terminal differs from what was logged (e.g. logged $12.75, the card was
charged $12.50 by mistake). The vendor is still owed the right amount,
but the SNAP total in the app no longer matches what the bank will
actually deposit — and there was no clean way to correct it.

**Now:** click **Adjust** on the transaction and choose **"Correct
Amount Collected."** Enter what was actually charged. The app:
- lowers the recorded amount to match the real charge (so SNAP totals
  match your bank deposit),
- **keeps the FAM match unchanged** (the match was correct — only the
  collection was short), and
- books the difference as **Unallocated Funds** (FAM absorbs it).

The vendor total never changes. A live preview shows exactly what will
happen before you confirm.

### 3. Vendor Reimbursement now shows an "ACH Enabled" column

The Vendor Reimbursement report — both in the app and on the Google
Sheet — now has an **ACH Enabled** column showing **Yes/No** per vendor,
so you can see at a glance which vendors are set up for ACH vs. check.

*Setup:* set each vendor's ACH status in **Settings → Vendors → (edit
vendor) → ACH Enabled.** The column fills in automatically on the next
sync, including for past months.

### 4. Sub-dollar token denominations (e.g. $0.50 JH Tokens)

Payment-method denominations can now be set below $1.00 — JH Tokens in
$0.50 increments, $0.25 coupons, etc. Previously the smallest allowed
was $1.00, which quietly snapped a typed $0.50 back to $1.00. Set it in
**Settings → Payment Methods → (edit method) → Denomination.**

---

## Also in this release (behind the scenes)

- **Quieter logs when offline.** While a market is offline, the app now
  records that sync is unavailable once when it drops and once when it
  returns, instead of repeating every few minutes — so the always-on
  closed-day sync above never clutters the logs, and real issues stay
  easy to spot. No effect on syncing itself.

---

## Upgrading

Use the in-app updater: **Settings → Updates → Check for Updates →
Download & Install.** The app closes, updates, and reopens on its own.
No database migration runs; existing data, settings, and cloud
configuration are all preserved. Manual download from the GitHub
release page also works if needed.
