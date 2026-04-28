"""Help library content — categories, articles, troubleshooting flows.

This file is the single source of truth for in-app help.  All content
is curated text (no AI generation).  When you change anything in the
user-facing surface, update the corresponding article HERE in the same
commit (see PROJECT_INSTRUCTIONS.md §8a).

Article body format:
    Markdown — rendered to HTML by Qt's QTextBrowser.
    Use ## for section headings, - for bullets, **bold**, *italic*,
    `inline code`, and standard hyperlinks.

Article IDs:
    Lowercase kebab-case slug (e.g. "market-day-open").
    Used for related_articles cross-references and search deep-links.
    Must be unique across the library.

When deleting an article, grep for its id in related_articles fields
elsewhere — broken cross-references are caught by the tests but you
should clean them up at edit time.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Category:
    """A grouping of related articles shown in the sidebar of the Help screen."""
    id: str
    name: str
    description: str
    sort_order: int


@dataclass(frozen=True)
class Article:
    """A single help article."""
    id: str                                          # unique slug
    category_id: str                                 # which category it belongs to
    title: str                                       # shown in the list
    body: str                                        # Markdown body
    keywords: tuple[str, ...] = ()                   # search terms
    related_articles: tuple[str, ...] = ()           # other article ids
    screen: Optional[str] = None                     # which screen this relates to


@dataclass(frozen=True)
class TroubleshootingFlow:
    """A decision-tree style troubleshooting entry."""
    id: str
    title: str                                       # symptom as a question
    symptom: str                                     # short user-facing description
    steps: tuple[str, ...]                           # ordered "if X, do Y"
    keywords: tuple[str, ...] = ()
    related_articles: tuple[str, ...] = ()


# ══════════════════════════════════════════════════════════════════
#   CATEGORIES
# ══════════════════════════════════════════════════════════════════

CATEGORIES: tuple[Category, ...] = (
    Category(
        id='getting-started',
        name='Getting Started',
        description='What FAM Manager does and how to get up and running.',
        sort_order=10,
    ),
    Category(
        id='during-market',
        name='During the Market',
        description='Recording receipts, processing payments, working with customers.',
        sort_order=20,
    ),
    Category(
        id='corrections',
        name='Corrections & Adjustments',
        description='Fixing mistakes, voiding transactions, post-confirmation edits.',
        sort_order=30,
    ),
    Category(
        id='fmnp',
        name='FMNP',
        description='Recording FMNP checks — both at the Payment Screen and on the FMNP Tracking Page.',
        sort_order=40,
    ),
    Category(
        id='reports',
        name='Reports & End of Day',
        description='Reading reports, exporting data, closing the market.',
        sort_order=50,
    ),
    Category(
        id='settings',
        name='Settings & Setup',
        description='Adding markets, vendors, payment methods, and connecting cloud sync.',
        sort_order=60,
    ),
    Category(
        id='sync',
        name='Sync & Cloud',
        description='Google Sheets sync, photo upload, and the sync indicator.',
        sort_order=70,
    ),
    Category(
        id='maintenance',
        name='Updates & Maintenance',
        description='Software updates, backups, and where your data lives.',
        sort_order=80,
    ),
)


# ══════════════════════════════════════════════════════════════════
#   ARTICLES
# ══════════════════════════════════════════════════════════════════

ARTICLES: tuple[Article, ...] = (

    # ── Getting Started ───────────────────────────────────────────

    Article(
        id='what-is-fam-manager',
        category_id='getting-started',
        title='What does FAM Manager do?',
        body="""\
FAM Market Manager is a Windows desktop application used at farmers
markets to record customer purchases and calculate **Food Assistance
Match (FAM)** subsidies.

It replaces the spreadsheet-and-paper-receipt process that markets used
previously, and produces:

- **Per-customer receipt records** with payment-method breakdowns
- **Vendor reimbursement reports** at the end of each month
- **FMNP check tracking** for state-funded match programs
- **End-of-day FAM Match reports** for program reporting

All data lives **on this laptop** in a single SQLite database file.  An
optional Google Sheets sync mirrors the data to a shared spreadsheet
for coordinators to review remotely.

The app works **fully offline** — internet is only required when you
choose to sync.
""",
        keywords=('introduction', 'overview', 'what', 'about'),
        related_articles=('first-run', 'where-data-lives'),
    ),

    Article(
        id='first-run',
        category_id='getting-started',
        title='First-run setup',
        body="""\
The first time you launch FAM Manager on a new laptop, an interactive
**tutorial overlay** walks you through the basics.  At the end of the
tutorial you can choose to **Load Defaults** — a one-click setup that
seeds the app with sample markets, vendors, and payment methods so you
can start exploring immediately.

If you'd rather start from a clean slate (recommended for production
use), close the tutorial without clicking Load Defaults.  You can then
configure your real markets, vendors, and payment methods from
**Settings**.

If your coordinator has already prepared a `.fam` settings file with
your organization's standard configuration, use **Settings → Import
Settings** to load it instead.
""",
        keywords=('setup', 'tutorial', 'first', 'install', 'load defaults'),
        related_articles=('load-defaults', 'import-settings', 'tutorial-mode'),
    ),

    Article(
        id='load-defaults',
        category_id='getting-started',
        title='Load Defaults vs. importing settings',
        body="""\
Two ways to populate a fresh FAM Manager install with starting data:

## Load Defaults

Click during the first-run tutorial.  Inserts:

- 3 sample markets
- ~23 sample vendors
- 6 sample payment methods (SNAP, FMNP, Food RX, JH Food Bucks, JH Tokens, Cash)

**FMNP is added but starts deactivated** — see *Activating FMNP for the
Payment Screen*.  This is intentional: most markets prefer to record
FMNP exclusively through the dedicated FMNP Entry screen.

Use this for: trying the app, training new volunteers, demos.

## Import Settings (.fam file)

**Settings → Import Settings**, choose a `.fam` file your coordinator
prepared.  Loads markets, vendors, payment methods, and per-market
assignments from the file.

Use this for: standardizing across multiple market laptops, restoring
a known configuration, sharing setup between coordinators.

A `.fam` file is plain text (pipe-separated values) that can be opened
in any text editor.
""",
        keywords=('defaults', 'sample', 'seed', 'import', 'settings file', 'fam file'),
        related_articles=('first-run', 'import-settings', 'fmnp-activate-payment'),
    ),

    Article(
        id='import-settings',
        category_id='getting-started',
        title='Importing a .fam settings file',
        body="""\
A coordinator can prepare a `.fam` settings file once and share it with
every market laptop, ensuring all devices have identical markets,
vendors, and payment methods.

## To export

**Settings → Export Settings**.  Saves a `.fam` file with everything
currently configured.  Share by email, USB, or shared drive.

## To import

**Settings → Import Settings**, choose the file.  The dialog shows a
preview of new vs. existing items.  Existing items (matched by name)
are skipped, never overwritten.

## What's included

- Markets and addresses
- Vendors and contact / reimbursement info
- Payment methods (name, match %, sort order, denomination)
- Vendor-to-market assignments
- Payment-method-to-market assignments

## What's NOT included

- Transactions, customer orders, FMNP entries (those are records, not
  configuration)
- Cloud sync credentials (those are device-specific and sensitive)
- Audit log
- Backups
""",
        keywords=('import', 'export', 'fam file', 'settings', 'configuration', 'share'),
        related_articles=('load-defaults', 'export-reports'),
    ),

    Article(
        id='tutorial-mode',
        category_id='getting-started',
        title='Re-running the tutorial',
        body="""\
The interactive tutorial runs automatically the first time the app is
launched.  After that, you can re-run it manually any time.

**How:** click the **Tutorial** button in the top-right header bar
(near the Sync and version indicators).

The tutorial walks through every screen with annotations explaining
what each section does.  It's safe to run on a laptop with real data
— no data is created, modified, or deleted.

You can also click **Load Defaults** at the end of the tutorial, which
adds sample data IF the database is empty.  If you already have data,
that step is silently skipped.
""",
        keywords=('tutorial', 'walkthrough', 'help', 'overlay'),
        related_articles=('first-run', 'load-defaults'),
    ),

    # ── During the Market ─────────────────────────────────────────

    Article(
        id='market-day-open',
        category_id='during-market',
        title='Opening a market day',
        body="""\
You must open a market day before you can record any receipts or FMNP
entries.  Only one market day can be open at a time.

## To open

1. Go to **Market** (sidebar)
2. Choose the market from the **Market** dropdown
3. The date defaults to today — change if needed
4. Click **Open Market Day**

The app:

- Creates a market_day record in the database
- Triggers an automatic **database backup** (under
  `%APPDATA%/FAM Market Manager/backups/`)
- Starts the **periodic auto-backup timer** — a fresh backup every
  5 minutes for the duration the market is open
- Enables the Receipt Intake, Payment, and FMNP Entry screens

If a market day was opened previously and never closed (e.g. the laptop
was shut down without closing), it remains open — you don't need to
re-open it.  Just resume entering receipts.
""",
        keywords=('open', 'market', 'start', 'begin', 'day'),
        related_articles=('market-day-close', 'market-day-reopen', 'backups'),
        screen='market_day',
    ),

    Article(
        id='enter-receipt',
        category_id='during-market',
        title='Entering a customer receipt',
        body="""\
The Receipt Intake screen records each receipt the customer hands you.
A "customer order" can contain multiple receipts (one per vendor visit
at the same market).

## Standard flow

1. **Receipt Intake** screen
2. Click **Start New Customer** (or pick a returning customer from the
   dropdown — see *Returning customer flow*)
3. For each vendor receipt:
   - Pick the vendor from the dropdown
   - Enter the receipt total (in dollars)
   - Optionally enter the receipt number for the vendor's records
   - Click **Add Receipt**
4. When the customer is done with all their vendors, click **Proceed
   to Payment**

The app moves you to the **Payment** screen with the order pre-loaded.

## Discarding a customer mid-entry

If a customer changes their mind or you started by mistake, click
**Discard Customer**.  This voids the in-progress order and all its
receipts.  No data is lost — voided records are still visible in the
Adjustments screen for audit purposes.
""",
        keywords=('receipt', 'intake', 'customer', 'enter', 'add'),
        related_articles=('returning-customer', 'split-payment', 'discard-customer'),
        screen='receipt_intake',
    ),

    Article(
        id='split-payment',
        category_id='during-market',
        title='Splitting payment across multiple methods',
        body="""\
Most receipts are paid with a mix of payment methods: SNAP for some,
Cash for the rest, maybe FMNP checks too.

## The Payment Screen

Each row represents one payment method.  Click **+ Add Payment Method**
to add another row.

For each row:

- **Method** dropdown — pick SNAP, Cash, etc.
- **Charge** — what the customer is paying with this method (the dollar
  value of their SNAP card swipe, the cash they hand over, etc.)
- The app automatically computes the **FAM Match** and **Total** based
  on the method's match percentage

The **Charge** field is capped at the receipt total — you can't
accidentally enter $999 on a $20 receipt.  If a row's charge plus the
match would exceed any active per-customer match cap, the cap is
applied automatically and shown in the breakdown.

## Auto-Distribute

Click **⚡ Auto-Distribute** to have the app fill the rows for you
based on the receipt total.  Useful when a customer pays entirely with
matched methods (SNAP + DUFB + FMNP) and you want the app to figure
out the right amounts.

When you click Auto-Distribute:

- Non-denominated rows (Cash, SNAP) are reset to zero, then filled
- Denominated rows (FMNP $5 checks) keep their charge — those represent
  physical tokens you've already counted from the customer
""",
        keywords=('payment', 'split', 'multiple', 'method', 'auto-distribute', 'distribute'),
        related_articles=('match-cap', 'auto-distribute-button', 'penny-reconciliation'),
        screen='payment',
    ),

    Article(
        id='auto-distribute-button',
        category_id='during-market',
        title='How Auto-Distribute works',
        body="""\
The **⚡ Auto-Distribute** button on the Payment Screen (and now also
on the Adjustment dialog as of v1.9.7) takes the receipt total and
spreads it across your selected payment methods automatically.

## The algorithm

1. **Denominated rows with a charge stay locked.**  If the customer
   handed you three $5 FMNP checks, the FMNP row keeps its $15 charge
   — the algorithm respects physical-check counts.
2. **Non-denominated rows are reset to zero**, then filled as
   "absorbers."  Cash, SNAP, and other non-denominated methods get the
   remainder of the receipt total spread across them.
3. **The match percentage is honored**: SNAP at 100% match means the
   customer pays half and the FAM match covers the other half.
4. **The match cap is honored** if active.  If the customer would
   exceed their daily $100 cap, charges are increased so the customer
   covers the deficit.

## When you'd use it

- Customer paid entirely with one matched method ($30 SNAP receipt →
  one click fills the SNAP row with $15 charge / $30 method amount)
- Mixed payment with denominated FMNP checks counted separately
- Adjusting an existing transaction with a new receipt total — instead
  of re-doing the math by hand

## When you wouldn't

- Cash-only receipts (the proportional rescale is faster)
- Single-method receipts where you'd rather type the number directly
""",
        keywords=('auto-distribute', 'auto distribute', 'redistribute', 'split', 'allocate'),
        related_articles=('split-payment', 'match-cap', 'penny-reconciliation'),
        screen='payment',
    ),

    Article(
        id='match-cap',
        category_id='during-market',
        title='The daily match cap explained',
        body="""\
Each market sets a **daily match limit** per customer (typically $100).
Once a customer's accumulated FAM match for the day reaches the cap,
additional matched purchases require the customer to cover the deficit.

## How it shows up

- The Payment Screen shows the customer's **remaining cap** at the top
  (e.g. "Remaining match: $42.50")
- The **Charge** field's maximum dynamically reduces as the rest of
  the row's match would push the customer over the cap
- If the customer would exceed the cap with the current entries, the
  app automatically increases their charge so the FAM match doesn't go
  over

## Returning customers

If a customer comes back later in the same market day, their previous
match is **already counted**.  The Returning Customer dropdown shows
how much match each prior customer has used.

## Configuring the cap

**Settings → Markets**, edit the market.  Set:

- **Daily Match Limit** — dollars per customer per market day
- **Match Limit Active** — uncheck to disable the cap entirely (rare)

A cap of $0 means "no FAM match this market" (e.g. a non-FAM market
day).  A cap of $0.01 effectively disables the match without disabling
the column on reports.
""",
        keywords=('cap', 'limit', 'maximum', 'daily', '$100', 'match limit'),
        related_articles=('returning-customer', 'split-payment', 'cap-warning'),
        screen='payment',
    ),

    Article(
        id='returning-customer',
        category_id='during-market',
        title='Returning customer flow',
        body="""\
A customer who already shopped at this market day can return for more
purchases.  Their accumulated match is tracked so the cap applies
across all their visits, not per-visit.

## To pick up a returning customer

1. **Receipt Intake** screen
2. Click the **Returning Customer** dropdown (next to "Start New
   Customer")
3. The dropdown shows each confirmed customer with their label and
   accumulated match (e.g. "C-007 — 2 receipts, $48.50 matched")
4. Pick the customer
5. Add new receipts as normal

When you proceed to Payment, the cap calculation includes this
customer's prior match.  If they're already at the cap, the Payment
Screen shows $0.00 remaining match.

## Customer labels

Customer labels are sequential per market day: C-001, C-002, etc.
They're anonymous — no names or personal info.  A returning customer
keeps their original label.
""",
        keywords=('returning', 'customer', 'come back', 'again', 'second visit'),
        related_articles=('match-cap', 'enter-receipt'),
        screen='receipt_intake',
    ),

    Article(
        id='draft-vs-confirm',
        category_id='during-market',
        title='Saving as Draft vs. Confirming',
        body="""\
The Payment Screen has two finishing buttons:

## Confirm Payment

The standard finish.  Marks every transaction in the order as
**Confirmed**, locks the payment breakdown, writes the audit log
entries, and triggers a sync to Google Sheets (if configured).

Confirmed transactions show up in reports.  They can still be adjusted
or voided from the Adjustments screen later, but every change is
audited.

## Save as Draft

Saves the current state but does NOT confirm.  The order stays in
**Draft** status — visible in the Pending Orders panel of Receipt
Intake but not yet in reports.

Use Save Draft when:

- A customer steps away mid-transaction and you need to clear the
  screen for the next customer
- You're not sure of a payment-method total and need to verify with
  the customer before confirming
- The vendor needs to recompute their receipt before you finalize

## Resuming a draft

**Receipt Intake → Pending Orders panel** shows all Draft orders for
the current market day.  Click **Resume** on the row to reopen the
Payment Screen with the draft loaded.

Drafts are kept across app restarts — closing the app does not lose
them.
""",
        keywords=('draft', 'confirm', 'save', 'finish', 'pending'),
        related_articles=('enter-receipt', 'discard-customer'),
        screen='payment',
    ),

    Article(
        id='discard-customer',
        category_id='during-market',
        title='Discarding a customer',
        body="""\
If you started a customer order by mistake or the customer changed
their mind, click **Discard Customer** (Receipt Intake screen) or
**Cancel** on the Payment Screen.

## What happens

- The customer order's status is set to **Voided**
- All in-progress receipts are voided
- Voided records remain in the database (audit trail) but are
  filtered out of reports

## Recovering

You cannot undo a void from the UI.  If a void was a mistake:

1. **Adjustments** screen
2. Search for the voided FAM transaction ID (find it in
   `fam_ledger_backup.txt` or via the audit log)
3. The transaction shows status "Voided" — there's no un-void button
4. You'd need to re-enter the receipts from scratch as a new customer

For this reason, only discard when you're sure.
""",
        keywords=('discard', 'cancel', 'void', 'mistake', 'abandon', 'remove'),
        related_articles=('void-transaction', 'enter-receipt'),
        screen='receipt_intake',
    ),

    Article(
        id='no-snap-no-match',
        category_id='during-market',
        title='Customer paying entirely with cash (no SNAP, no match)',
        body="""\
A customer can shop without using any matched payment method.  The
process is identical, just with one **Cash** row covering the full
receipt total.

## Steps

1. Receipt Intake — enter the receipts as normal
2. Payment Screen — the app starts with one row
3. Pick **Cash** (or **Check**) as the method
4. Enter the receipt total as the charge
5. The FAM Match column shows $0.00 (Cash is not matched)
6. Confirm

The transaction still gets a FAM transaction ID and shows up in
reports, but the FAM Match column is zero.  This is correct — the
program serves cash customers too, just without subsidy.
""",
        keywords=('cash', 'no match', 'no snap', 'unmatched', 'plain'),
        related_articles=('split-payment', 'enter-receipt'),
        screen='payment',
    ),

    Article(
        id='penny-reconciliation',
        category_id='during-market',
        title='Penny reconciliation — small rounding differences',
        body="""\
You may occasionally see a payment breakdown with a 1-cent difference
between what looks "expected" and what shows in the row totals.  This
is correct behavior — not a bug.

## Why it happens

Match percentages can produce fractional cents (e.g. 33% match on a
$10 receipt = $3.33333... match).  Money is stored in whole cents, so
the calculator rounds.  Small rounding drift would otherwise mean
"customer paid $4.99 + match $5.00 ≠ receipt $10.00."

## The fix

The calculator detects when allocation drift is exactly 1 cent and
**absorbs the penny into the FAM match** of the largest matched line
item.  This guarantees:

- Customer + Match = Receipt Total **exactly to the penny**, every time
- The customer is never charged the rounding penny — FAM absorbs it
- Reports reconcile perfectly across all three views (UI, ledger, sync)

## What you'll see

A FAM match might be $5.01 instead of the "expected" $5.00 on a tricky
receipt total.  This is correct.  You don't need to do anything.
""",
        keywords=('penny', 'rounding', 'cent', '0.01', 'difference', 'reconciliation'),
        related_articles=('split-payment', 'match-cap'),
    ),

    Article(
        id='market-day-close',
        category_id='during-market',
        title='Closing the market day',
        body="""\
At the end of the market, close the day to lock it down.

## To close

1. **Market** screen
2. Confirm there are no outstanding **Draft** orders (check the Pending
   Orders panel on Receipt Intake — drafts won't appear in reports
   until confirmed)
3. Click **Close Market Day**

The app:

- Sets the market day's status to **Closed**
- Triggers a final **database backup** (labeled `market_close`)
- Runs a **Cloud Sync** if configured (so today's data lands in the
  Google Sheet before you put the laptop away)
- Stops the periodic auto-backup timer
- Disables Receipt Intake / Payment / FMNP Entry screens until you
  open another market day

You can still view reports and run Adjustments after the market day
is closed.

## Reopening

If you discover a mistake later, you can reopen the market day —
**Market → Reopen Market Day** — make the correction, then close
again.  Reopen is audited.
""",
        keywords=('close', 'end', 'finish', 'market day', 'wrap up'),
        related_articles=('market-day-reopen', 'draft-vs-confirm', 'sync-overview'),
        screen='market_day',
    ),

    Article(
        id='market-day-reopen',
        category_id='during-market',
        title='Reopening a closed market day',
        body="""\
A closed market day can be reopened to add or correct entries.  Use
this for late corrections, missed receipts, or mistakes spotted after
end-of-day reports were generated.

## To reopen

1. **Market** screen
2. Click **Reopen Market Day**
3. Confirm

The market day's status returns to **Open**.  Receipt Intake, Payment,
and FMNP Entry are re-enabled.

When you're done, close the market day again — this triggers another
sync so the corrections land in the Google Sheet.

## Audit trail

Every open / close / reopen is recorded in the audit log with
timestamp and user.  Coordinators reviewing the reports can see when
the day was reopened and what changed afterward.
""",
        keywords=('reopen', 're-open', 'closed', 'corrections', 'late'),
        related_articles=('market-day-close', 'adjust-transaction'),
        screen='market_day',
    ),

    Article(
        id='offline-operation',
        category_id='during-market',
        title='Working offline (no internet)',
        body="""\
FAM Manager is **fully functional without an internet connection**.

## What works offline

- Opening / closing market days
- Entering receipts
- Processing payments
- Adding FMNP entries
- Viewing reports
- All adjustments and voids
- Local backups (every 5 min, plus open / close)

## What does NOT work offline

- **Cloud Sync to Google Sheets** — the sync indicator will show "No
  network" or "Sync failed."  Your data is safe locally; sync will
  resume next time you click **Sync to Cloud** with internet.
- **Photo upload to Google Drive** — photos are stored locally and
  queued.  They upload automatically the next time the laptop has
  internet.
- **Auto-update check** — the Settings screen will show "could not
  reach update server."

## When the market is offline by design

If the market venue has no Wi-Fi at all, you can run the entire day
offline and sync once at home.  Open + close + every transaction is
recorded locally and the sync at end-of-day pushes everything in one
batch.
""",
        keywords=('offline', 'no internet', 'no wifi', 'disconnected', 'no network'),
        related_articles=('sync-indicator', 'sync-failed', 'photo-upload-pending'),
    ),

    # ── Corrections & Adjustments ─────────────────────────────────

    Article(
        id='adjust-transaction',
        category_id='corrections',
        title='Adjusting a confirmed transaction',
        body="""\
Confirmed transactions can be adjusted from the Adjustments screen.
Common reasons: vendor sent a corrected receipt, the cashier picked
the wrong vendor, a payment-method total was off.

## To adjust

1. **Adjustments** screen
2. Filter / search to find the transaction
3. Click **Adjust** on the row
4. The Adjustment dialog opens with the current values pre-loaded
5. Change what needs to change:
   - Receipt total (other rows rescale proportionally; click ⚡
     Auto-Distribute for a fresh redistribution)
   - Vendor
   - Payment-method breakdown
   - Reason and notes (required for adjustments)
6. Click **OK** to save

## What changes

- Transaction status changes from **Confirmed** to **Adjusted**
- The audit log records every changed field with old + new values
- Reports update immediately to reflect the new totals
- A cloud sync is triggered (if configured)

## What stays

- The original FAM transaction ID
- The customer order linkage
- Any photos attached to the receipt
- Historical reports already exported are unchanged (snapshots)
""",
        keywords=('adjust', 'edit', 'correct', 'fix', 'modify', 'change'),
        related_articles=('void-transaction', 'auto-distribute-button',
                          'audit-log'),
        screen='admin',
    ),

    Article(
        id='void-transaction',
        category_id='corrections',
        title='Voiding a transaction',
        body="""\
Voiding a transaction marks it as removed.  Voided transactions remain
in the database for audit purposes but are filtered out of reports and
totals.

## When to void

- The transaction was a complete mistake (wrong customer, duplicate
  entry)
- The customer cancelled the entire purchase
- The vendor is reversing the sale

If only some details are wrong, **adjust** instead of voiding.

## To void

1. **Adjustments** screen
2. Find the transaction
3. Click **Void**
4. Confirm

The status changes to **Voided**.  Reports update.  Sync runs.

## Voiding is one-way

There is no "un-void" button.  If a void was a mistake, you must
re-enter the receipts as a new customer order.  The original voided
transaction stays in the audit log forever as a record of what
happened.
""",
        keywords=('void', 'cancel', 'remove', 'delete', 'reverse'),
        related_articles=('adjust-transaction', 'void-vs-adjust', 'audit-log'),
        screen='admin',
    ),

    Article(
        id='void-customer-order',
        category_id='corrections',
        title='Voiding an entire customer order',
        body="""\
If a customer order has multiple receipts and you want to void all of
them at once (rather than one at a time), use the customer-order
controls on Receipt Intake.

## During data entry (mid-customer)

If you're still in Receipt Intake with the customer in progress, click
**Discard Customer**.  This voids the order and all attached receipts.

## After confirmation (Pending Orders)

If a Draft order was saved but never confirmed:

1. **Receipt Intake** → **Pending Orders** panel
2. Click the **X** (delete) button on the row
3. Confirm

The order and all its receipts are voided in a single transaction.

## After full confirmation

If every receipt was confirmed and you want to void them all:

1. **Adjustments** screen
2. Filter by customer label (e.g. C-007)
3. Void each transaction individually

There's no bulk-void in the Adjustments screen — that's intentional,
to make sure each void is a deliberate action.
""",
        keywords=('void', 'order', 'customer', 'discard', 'cancel'),
        related_articles=('void-transaction', 'discard-customer'),
        screen='receipt_intake',
    ),

    Article(
        id='void-vs-adjust',
        category_id='corrections',
        title='When to void vs. when to adjust',
        body="""\
Both void and adjust are audited; both update reports.  The difference
is in what stays.

## Void when

- The transaction shouldn't exist at all
- The customer never bought anything (the entry is purely a mistake)
- A duplicate transaction was entered

## Adjust when

- The receipt total is wrong but the customer did purchase
- The vendor was selected incorrectly but the amount is right
- The payment-method breakdown needs correction (e.g. SNAP entered as
  Cash)
- A photo was missed and you need to re-attach

**Rule of thumb:** if the customer received goods, adjust.  If no
goods changed hands, void.

Adjusted transactions retain the original FAM transaction ID and
customer-order linkage; voided transactions retain the audit trail
but disappear from reports.
""",
        keywords=('void', 'adjust', 'difference', 'when'),
        related_articles=('void-transaction', 'adjust-transaction'),
        screen='admin',
    ),

    Article(
        id='audit-log',
        category_id='corrections',
        title="The audit log — what's recorded",
        body="""\
Every change in the database is recorded in an **append-only audit
log**.  Nothing is ever silently overwritten.

## What gets logged

| Action | Logged |
|---|---|
| Transaction created | ✓ (CREATE) |
| Transaction confirmed | ✓ (CONFIRM) |
| Transaction adjusted | ✓ (ADJUST, one row per changed field) |
| Transaction voided | ✓ (VOID) |
| Customer order created | ✓ (CREATE) |
| Customer order voided | ✓ (VOID) |
| FMNP entry created | ✓ (INSERT) |
| FMNP entry edited | ✓ (UPDATE, one row per changed field) |
| FMNP entry deleted | ✓ (DELETE) |
| Market day opened / closed / reopened | ✓ (OPEN / CLOSE / REOPEN) |
| Payment line items saved | ✓ (PAYMENT_SAVED) |

## Where to view

- **Adjustments screen** has an Audit Log panel showing the most
  recent 20 entries
- **Reports → Activity Log** tab shows up to 500 entries with filters
  by date, action, and area
- The full log is in the SQLite database; it can be queried directly
  for forensic analysis

## What's recorded per entry

- Timestamp (in Eastern time)
- Action (CREATE, ADJUST, VOID, etc.)
- Who made the change (volunteer name from the entered_by field)
- The specific field changed (for ADJUST and UPDATE)
- Old value and new value
- Reason code and notes (for adjustments)
- App version and device ID
""",
        keywords=('audit', 'log', 'history', 'who', 'when', 'changed', 'tracked'),
        related_articles=('adjust-transaction', 'void-transaction'),
        screen='admin',
    ),

    Article(
        id='cap-warning',
        category_id='corrections',
        title='Cap exceeded warning — what it means',
        body="""\
If the Payment Screen warns you that a customer has exceeded their
match cap, here's what's happening and what to do.

## What the cap is

Each market sets a per-customer daily match limit (typically $100).
Across all of a customer's purchases on one market day, the FAM match
cannot exceed this amount.

## What the warning shows

- The customer's already-used match (from prior receipts that day)
- The amount of additional match the current breakdown would add
- The total which exceeds the cap

## What to do

The Charge field's max is automatically reduced so you can't add more
match-funded charge than the remaining cap allows.  The customer must
cover the deficit with non-matched payment (Cash, Check).

If the cap math seems wrong:

1. Verify it's the right customer (returning customer dropdown)
2. Check the customer's prior match in the dropdown listing
3. If you suspect a previous transaction was the wrong customer
   label, fix it via Adjustments
""",
        keywords=('cap', 'exceeded', 'warning', 'over', 'limit'),
        related_articles=('match-cap', 'returning-customer', 'adjust-transaction'),
        screen='payment',
    ),

    # ── FMNP ──────────────────────────────────────────────────────

    Article(
        id='fmnp-overview',
        category_id='fmnp',
        title='FMNP — the two ways to record it',
        body="""\
FMNP (Farmers Market Nutrition Program) checks can be recorded **two
different ways** depending on **who applied the match** at the moment
of sale.

## Path 1: FMNP via Payment Screen

- Use when **FAM provides the match** at the FAM table
- The check appears as a payment-method row alongside SNAP, Cash, etc.
- The FAM match percent is calculated and applied
- Photos and receipts are linked to the customer order
- The check counts toward the customer's daily match cap

## Path 2: FMNP via FMNP Entry screen (the dedicated tracking page)

- Use when **the vendor matched the check at the booth** — they treated
  the check at a higher dollar value (e.g. a $5 check counted as $10
  of food)
- The vendor cashes the original check directly with the FMNP program
- **FAM reimburses the vendor the face value** (no match percent
  added) so the vendor is made whole on the match they applied
- This is the standard pattern for vendor-direct FMNP and the most
  common path

## How to know which path

Ask your coordinator how your market handles FMNP.  Most markets use
the FMNP Entry screen exclusively (vendor matches at the booth, FAM
reimburses face value).  Some markets use the Payment Screen path for
programs where FAM applies the match itself.

If you're unsure, default to **FMNP Entry screen**.  That's the
correct choice when the vendor handles the match.

## Why these are separate

Mixing the two paths would cause double-counting.  v1.9.7 ensures
they're stored in separate tables so reports correctly distinguish
**FAM-matched FMNP** (Path 1, where a match percent is applied)
from **FMNP (External)** (Path 2, where FAM reimburses face value
because the vendor matched at the booth).
""",
        keywords=('fmnp', 'overview', 'two paths', 'difference'),
        related_articles=('fmnp-via-payment', 'fmnp-via-tracking',
                          'fmnp-activate-payment'),
    ),

    Article(
        id='fmnp-via-payment',
        category_id='fmnp',
        title='Recording FMNP via the Payment Screen',
        body="""\
Use this path when **FAM provides the match** for the FMNP check (not
the vendor).

## Prerequisites

FMNP must be **active** as a payment method.  By default in v1.9.8+,
FMNP is **inactive** — see *Activating FMNP for the Payment Screen*.

## Steps

1. Receipt Intake → record the customer's vendor receipts as usual
2. Proceed to Payment
3. Click **+ Add Payment Method** and select **FMNP**
4. The FMNP row appears with denomination of $5 (FMNP checks come in
   $5 increments)
5. Use the up/down stepper to enter the **number of checks** the
   customer is handing over
6. The Charge auto-calculates ($5 × number of checks)
7. The FAM Match auto-calculates based on the configured match %
8. Photo of the checks (if photo-required is set in Settings)
9. Confirm

## What gets recorded

- A `payment_line_items` row with `method_name_snapshot = 'FMNP'`
- The FAM match amount applied
- The check count is captured (used for vendor reimbursement reports)
- Photos uploaded to Drive (if configured)

This pattern is uncommon in most markets.  If your market has the
vendor matching the FMNP check directly, use the FMNP Entry screen
instead.
""",
        keywords=('fmnp', 'payment', 'screen', 'check', 'fam match'),
        related_articles=('fmnp-overview', 'fmnp-via-tracking',
                          'fmnp-activate-payment'),
        screen='payment',
    ),

    Article(
        id='fmnp-via-tracking',
        category_id='fmnp',
        title='Recording FMNP via the Entry screen (most common)',
        body="""\
Use this path when **the vendor matched the FMNP check at the booth** —
the standard pattern.  Participating-FAM vendors typically treat a
$5 FMNP check as $10 worth of food (they apply the match themselves at
the moment of sale).  Two things then happen at end-of-month:

- The **vendor cashes the original FMNP check** directly with the FMNP
  program — they get the face value back ($5 in our example).
- **FAM reimburses the same face value** ($5) so the vendor ends up
  made whole on the match they gave away.

So FAM does **not** add a match percent on top of these checks (the
vendor already did) — but FAM **does** reimburse the face value at
end-of-month.

## Steps

1. **FMNP Entry** screen (sidebar)
2. Pick the market day
3. Pick the vendor who took the check(s)
4. Enter the **amount** in dollars (must be a multiple of $5 — the
   FMNP denomination)
5. Enter **check count** (optional but recommended for reconciliation)
6. Notes (optional)
7. Photo of the checks (if photo-required is set)
8. Click **Add FMNP Entry**

The entry is saved.  It appears in:

- The FMNP Entries list on this same screen (current market day)
- The **Detailed Ledger** report (one row per FMNP entry, separate
  from transactions)
- The **Vendor Reimbursement** report under the **FMNP (External)**
  column for that vendor — and the amount **is included in that
  vendor's "Total Due to Vendor"**, since FAM is reimbursing it
- The **FAM Match Report** as a separate "FMNP (External)" line with
  $0.00 in the FAM Match column (no match percentage was applied by
  FAM — the dollars in this column reflect the face-value
  reimbursement, not match)
- The Google Sheets FMNP Entries tab (if sync is configured)

## Editing or deleting

Click the row to edit.  Click the X to delete (soft delete — record
preserved with status='Deleted').

The FMNP Entry screen works **regardless of whether FMNP is active as
a payment method** in Settings.  The two paths are completely
independent.
""",
        keywords=('fmnp', 'entry', 'tracking', 'check', 'vendor', 'external'),
        related_articles=('fmnp-overview', 'fmnp-via-payment',
                          'fmnp-edit-delete', 'fmnp-photo'),
        screen='fmnp',
    ),

    Article(
        id='fmnp-activate-payment',
        category_id='fmnp',
        title='Activating FMNP as a Payment Screen option',
        body="""\
By default in v1.9.8+, FMNP is **inactive** as a payment method —
meaning it does NOT appear as a row option on the Payment Screen.
This is intentional: most markets exclusively use the FMNP Entry
screen because the vendor handles the FMNP match directly.

## To activate FMNP for the Payment Screen

1. **Settings** → **Payment Methods**
2. Find the **FMNP** row
3. Click **Activate**
4. FMNP now appears as an option when adding a payment method on the
   Payment Screen

## To deactivate again

Same path, click **Deactivate**.

## What this does NOT affect

- The **FMNP Entry screen** — works regardless of active state.  All
  FMNP entry creation, editing, deletion, and reporting goes through
  this screen and is unaffected.
- **Existing transactions** that already used FMNP as a payment method
  — they remain in reports.  Deactivating doesn't erase history.
- **Photo and denomination settings** — those stay configured even
  when FMNP is inactive.

Use this toggle when the policy of who-matches-FMNP changes for your
market.  Most markets leave it inactive permanently.
""",
        keywords=('fmnp', 'activate', 'deactivate', 'payment screen',
                  'enable', 'disable'),
        related_articles=('fmnp-overview', 'fmnp-via-payment'),
        screen='settings',
    ),

    Article(
        id='fmnp-edit-delete',
        category_id='fmnp',
        title='Editing or deleting an FMNP entry',
        body="""\
FMNP entries can be edited or deleted from the FMNP Entry screen at
any time.  All changes are audited.

## To edit

1. **FMNP Entry** screen
2. Click the **pencil** icon on the entry row (or the row itself,
   depending on theme)
3. The form at the top populates with the entry's current values
4. Change what needs to change (amount, vendor, check count, notes,
   photo)
5. Click **Update FMNP Entry**

Each changed field produces a separate audit log row with old + new
values.  Unchanged fields are not logged.

## To delete

1. Click the **X** button on the entry row
2. Confirm

The entry's status is set to **Deleted** (soft delete).  The row
remains in the database but is filtered out of all reports and the
FMNP Entries display.  The audit log records the deletion.

## After edit or delete

A cloud sync is triggered automatically (60-second cooldown applies).
The Google Sheets FMNP Entries tab updates to reflect the change.
""",
        keywords=('fmnp', 'edit', 'delete', 'modify', 'remove', 'change'),
        related_articles=('fmnp-via-tracking', 'audit-log'),
        screen='fmnp',
    ),

    Article(
        id='fmnp-photo',
        category_id='fmnp',
        title='FMNP photo requirements',
        body="""\
Photos of FMNP checks can be made **mandatory**, **optional**, or
**off** depending on your market's reconciliation requirements.

## Configuring

**Settings → Payment Methods → FMNP → Edit → Photo Required**

Three options:

- **Off** — no photo prompt; entries can be saved without a photo
- **Optional** — photo prompt appears but isn't required to save
- **Mandatory** — entries cannot be saved without a photo (one photo
  per check)

## How photos are matched to checks

If `check_count` is 3, the form shows 3 photo slots.  In Mandatory
mode, all 3 must be filled before save is allowed.  Photos are
numbered automatically.

## Where photos go

Photos are:

1. **Resized** to 1920px on the longest side, JPEG quality 85
2. **Stored locally** in `%APPDATA%/FAM Market Manager/photos/`
3. **Uploaded to Google Drive** on the next sync (if configured)
4. **Deduplicated** by SHA-256 content hash — uploading the same
   photo twice doesn't create a duplicate file in Drive

The Drive folder is shared with the service account configured in
Settings → Cloud Sync.  See *Connecting Google Drive*.
""",
        keywords=('fmnp', 'photo', 'image', 'mandatory', 'optional', 'required'),
        related_articles=('fmnp-via-tracking', 'connect-drive'),
        screen='fmnp',
    ),

    # ── Reports & End of Day ──────────────────────────────────────

    Article(
        id='fam-match-report',
        category_id='reports',
        title='Reading the FAM Match report',
        body="""\
The FAM Match report breaks down match dollars by payment method.
Coordinators use it to report program impact to funders.

## Where to find it

**Reports** screen → **FAM Match** tab

## What's shown

| Column | Meaning |
|---|---|
| Payment Method | SNAP, FMNP, DUFB, etc. |
| Total Allocated | Total method_amount across all transactions |
| Total FAM Match | Sum of FAM match given on this method |

A separate **FMNP (External)** row appears at the bottom showing the
total amount of FMNP checks logged via the FMNP Entry screen.  Note:
**Total FAM Match for FMNP (External) is always $0** — those checks
were matched by the vendor, not by FAM.

## Filters

- Date range (from / to)
- Markets (multi-select)
- Payment methods (multi-select)
- Status (Confirmed only, or include Adjusted)

## Summary cards

Top of the screen:

- Total Receipts
- Customer Paid
- FAM Match
- FMNP Checks (renamed from "FMNP Match" in v1.9.4 to clarify these
  are vendor-reimbursed checks, not FAM-matched amounts)
""",
        keywords=('fam match', 'report', 'match', 'totals', 'summary'),
        related_articles=('vendor-reimbursement', 'detailed-ledger',
                          'export-reports'),
        screen='reports',
    ),

    Article(
        id='vendor-reimbursement',
        category_id='reports',
        title='Reading the Vendor Reimbursement report',
        body="""\
The Vendor Reimbursement report is the monthly check-cutting source
of truth.  Each row tells you exactly how much to reimburse each
vendor.

## Where to find it

**Reports** screen → **Vendor Reimbursement** tab

## What's shown

| Column | Meaning |
|---|---|
| Market Name | The market the receipt was at |
| Vendor | The vendor receiving reimbursement |
| Month | The reimbursement period |
| Date(s) | Which market days are covered |
| Total Due to Vendor | The full amount FAM owes the vendor — includes everything in the per-method columns AND the FMNP (External) face-value reimbursement |
| {Per-method columns} | Breakdown by payment method (SNAP, FMNP-via-Payment-Screen, DUFB, etc.) |
| FMNP (External) | Face value of FMNP checks the vendor matched at the booth.  FAM reimburses this amount because the vendor applied the FAM match themselves at the booth and needs to be made whole. |
| Check Payable To | The vendor's check-payable name |
| Address | Mailing address |

## How FMNP (External) folds into Total Due

The **FMNP (External)** column is **included** in the "Total Due to
Vendor" total.  The vendor cashed the original FMNP check directly
with the program, but FAM separately reimburses the face value
because the vendor applied a 2x match at the booth (e.g. accepted a
$5 FMNP check as $10 of food).  Cashed check + FAM reimbursement
= the matched value the vendor gave the customer.

## Filters

Date range, markets, vendors.

## Workflow

1. End of month, filter to the month
2. Export to CSV
3. Cross-check against vendor receipts
4. Issue checks for the **Total Due to Vendor** amount — that's the
   full reimbursement, including FMNP (External) face values
""",
        keywords=('vendor', 'reimbursement', 'check', 'monthly', 'pay'),
        related_articles=('fam-match-report', 'detailed-ledger', 'export-reports'),
        screen='reports',
    ),

    Article(
        id='detailed-ledger',
        category_id='reports',
        title='Reading the Detailed Ledger',
        body="""\
The Detailed Ledger is the line-by-line transaction record — one row
per receipt across all markets and dates.

## Where to find it

**Reports** screen → **Detailed Ledger** tab

## What's shown

Each row:

- FAM Transaction ID
- Vendor
- Receipt Total
- Status (Confirmed / Adjusted / Voided)
- Created timestamp
- Customer label (e.g. C-007)
- Customer Paid
- FAM Match
- Methods (e.g. "SNAP: $20.00, Cash: $5.00")

FMNP entries (from the FMNP Entry screen, not Payment Screen) appear
as rows too, with no customer label.  Their FAM Match column shows
the same as the receipt total — but this is **vendor reimbursement**,
not actual FAM match dollars.

## Filters

Date range, markets, vendors, payment types, status.

## Use cases

- Auditing a specific transaction
- Reconciling against vendor receipts
- Investigating a discrepancy reported by a vendor
- Pulling raw data for an external analysis
""",
        keywords=('ledger', 'detailed', 'transactions', 'rows', 'line items'),
        related_articles=('fam-match-report', 'vendor-reimbursement',
                          'export-reports'),
        screen='reports',
    ),

    Article(
        id='filter-reports',
        category_id='reports',
        title='Filtering reports',
        body="""\
Every report has the same filter row at the top of the screen.
Changes to filters apply to all tabs simultaneously.

## Filter types

- **Date range** — From / To.  Defaults to current month.  Can be set
  to "All time" by clearing both fields.
- **Markets** — multi-select dropdown.  Default: all markets.
- **Vendors** — multi-select.  Default: all vendors.
- **Payment Types** — multi-select.  Default: all payment methods.
- **Status** — Confirmed only / Confirmed + Adjusted / All.  Default:
  Confirmed + Adjusted (excludes Voided).

## Applying filters

Filters apply automatically as you change them.  No "Apply" button.

## Reset filters

Click **Reset Filters** in the filter row to return to defaults.

## Filter persistence

Filter state does not persist across app restarts — every launch
starts with default filters.
""",
        keywords=('filter', 'date range', 'select', 'limit', 'narrow'),
        related_articles=('fam-match-report', 'vendor-reimbursement',
                          'detailed-ledger'),
        screen='reports',
    ),

    Article(
        id='export-reports',
        category_id='reports',
        title='Exporting reports to CSV',
        body="""\
Every report tab has an **Export CSV** button that produces a
spreadsheet-friendly file with the **currently filtered data**.

## To export

1. **Reports** screen → pick the tab you want
2. Apply filters as needed
3. Click **Export {Report Name} CSV** at the top right
4. Choose a save location.  The default filename includes the report
   type and date range.
5. Open the resulting file in Excel, Google Sheets, or any text editor

## What's exported

- Exactly what's shown on screen (same filters, same columns)
- Headers in row 1
- Numeric values are unformatted (no `$` prefix) for easy spreadsheet
  math

## Multiple reports

Each report tab has its own CSV export.  To export everything, switch
tabs and click each export button in turn.

## Auto-generated filenames

Format: `{ReportName}_{Market}_{StartDate}_{EndDate}.csv`

You can change the filename in the save dialog.
""",
        keywords=('export', 'csv', 'spreadsheet', 'excel', 'download'),
        related_articles=('filter-reports', 'fam-match-report'),
        screen='reports',
    ),

    Article(
        id='fmnp-external-meaning',
        category_id='reports',
        title='What "FMNP (External)" means in reports',
        body="""\
The **FMNP (External)** label appears in multiple reports.  It refers
specifically to FMNP checks recorded through the **FMNP Entry screen**
(not the Payment Screen).

## Why "External"

The match for these checks was applied **externally** — at the
vendor's booth, by the vendor — rather than at the FAM table.  A
participating-FAM vendor treats an FMNP check at double face value
(a $5 check counts as $10 of food).  The vendor cashes the original
check directly with the FMNP program for the face value, and **FAM
reimburses the same face value** at end-of-month so the vendor ends
up whole on the match they applied.

So FAM's spend on FMNP (External) is the **face value of the check**,
not a match percentage on top.

## Where you'll see it

- **Vendor Reimbursement Report** — its own column showing each
  vendor's FMNP (External) total.  The amount **is included in that
  vendor's "Total Due to Vendor"** because FAM is reimbursing it.
- **FAM Match Report** — shows as a separate row.  The Total FAM
  Match column reads $0.00 for this row (no match percent was
  applied — only face-value reimbursement).
- **Detailed Ledger** — shown as ledger rows with no customer label
- **Summary cards** at the top of the FAM Match Report — labeled
  "FMNP Checks" (was renamed from "FMNP Match" in v1.9.4 to make
  clear these are face-value reimbursements, not match dollars)

## Why this distinction matters

Without separation, the same FMNP dollar could be double-counted:

- Once as a customer payment matched by FAM (Path 1, Payment Screen)
- Once as vendor FMNP-External reimbursement (Path 2, FMNP Entry)

The separation is structural — different database tables — so
double-counting is impossible by design.  FMNP (External) entries
are FAM dollars going to vendor reimbursement; FAM-matched FMNP
entries are FAM dollars going to customer match.  Different flows,
different tables.
""",
        keywords=('fmnp external', 'external', 'fmnp', 'reports', 'meaning'),
        related_articles=('fmnp-overview', 'fmnp-via-tracking',
                          'fam-match-report', 'vendor-reimbursement'),
        screen='reports',
    ),

    # ── Settings & Setup ──────────────────────────────────────────

    Article(
        id='add-market',
        category_id='settings',
        title='Adding a market',
        body="""\
**Settings → Markets → Add Market**

Required:

- **Market Name** — a unique label (e.g. "Bethel Park Farmers Market")
- **Address** — optional but recommended for end-of-day reports
- **Daily Match Limit** — per-customer match cap in dollars (default
  $100)
- **Match Limit Active** — whether the cap should enforce.  Uncheck
  for unlimited match (rare).

After save, the new market is available in the Market screen's
dropdown.  Existing vendors and payment methods are NOT automatically
assigned to it — assign them manually via the buttons on the market
row.

## Editing

Click the pencil icon on the market row.  All fields can be changed.
Changes apply immediately to future market days; historical reports
keep the market name as it was at the time of the transaction.
""",
        keywords=('market', 'add', 'create', 'new', 'location'),
        related_articles=('add-vendor', 'add-payment-method', 'match-cap'),
        screen='settings',
    ),

    Article(
        id='add-vendor',
        category_id='settings',
        title='Adding a vendor',
        body="""\
**Settings → Vendors → Add Vendor**

Required:

- **Vendor Name**

Recommended for reimbursement reports:

- **Check Payable To** — the legal name on reimbursement checks
- **Street, City, State, ZIP** — mailing address
- **ACH Enabled** — check if the vendor accepts ACH instead of paper
  checks

## Assigning to markets

After save, assign the vendor to specific markets via **Settings →
Markets → {market name} → Vendors**.  Only assigned vendors appear in
the Receipt Intake vendor dropdown for that market.

## Editing

Pencil icon on the vendor row.  Most fields editable.  Renaming a
vendor preserves historical reports (snapshots).

## Deactivating

Toggle the vendor's active state to remove them from new transaction
options without losing history.  Inactive vendors don't appear in
Receipt Intake but their historical transactions remain in reports.
""",
        keywords=('vendor', 'add', 'create', 'new', 'farmer'),
        related_articles=('add-market', 'add-payment-method'),
        screen='settings',
    ),

    Article(
        id='add-payment-method',
        category_id='settings',
        title='Adding a payment method',
        body="""\
**Settings → Payment Methods → Add Payment Method**

Required:

- **Name** — short label (e.g. "DUFB", "Cash", "JH Tokens")
- **Match %** — 0 for unmatched (Cash), 100 for 1:1 match (SNAP), 200
  for 2:1 match (DUFB), etc.
- **Sort Order** — controls display order on the Payment Screen
  (lower number = higher placement)

Optional:

- **Denomination** — for token/check methods sold in fixed amounts
  (e.g. FMNP at $5).  Uses a stepper UI instead of free-form entry.
- **Photo Required** — Off / Optional / Mandatory (currently used by
  FMNP only)

## Assigning to markets

Same pattern as vendors — assign each payment method to the markets
where it's accepted.

## Renaming, deactivating, deleting

- **Rename**: pencil icon.  Historical reports keep the old name as a
  snapshot.
- **Deactivate**: hides from Payment Screen options without losing
  history.  Re-activate later if needed.
- **Delete**: not supported — deactivate instead, to preserve audit
  history.

## Special: FMNP

FMNP is the only payment method with hard-coded behavior elsewhere
(the FMNP Entry screen reads FMNP's denomination and photo settings).
Its name cannot be changed.  Its is_active state can.
""",
        keywords=('payment method', 'add', 'create', 'new', 'snap',
                  'dufb', 'tokens'),
        related_articles=('add-market', 'add-vendor', 'fmnp-activate-payment'),
        screen='settings',
    ),

    Article(
        id='match-limit-config',
        category_id='settings',
        title='Configuring the match limit',
        body="""\
The match limit is set per market.  It caps the per-customer FAM
match across all their transactions on a single market day.

## To configure

1. **Settings → Markets**
2. Find the market, click the pencil icon
3. Edit:
   - **Daily Match Limit** — dollar amount
   - **Match Limit Active** — checkbox to enable/disable the cap
4. Save

## Common values

- **$100** — typical FAM market default
- **$50** — half-day or partial-program markets
- **$0** — set to zero plus uncheck Match Limit Active for a market
  day with no FAM match (the column still tracks zero match per
  transaction; reports stay consistent)

## How the cap is applied

- Calculated **per customer per market day**, summing match across
  all that customer's confirmed and adjusted transactions
- The Payment Screen shows remaining cap at the top
- The Charge field's max is reduced dynamically so a single payment
  row can't exceed the remaining cap
- A small 1-cent overshoot can occur due to penny-reconciliation
  rounding (acceptable; absorbed by FAM)
""",
        keywords=('match limit', 'cap', 'daily', '$100', 'configure'),
        related_articles=('match-cap', 'add-market', 'cap-warning'),
        screen='settings',
    ),

    Article(
        id='connect-sheets',
        category_id='settings',
        title='Connecting Google Sheets sync',
        body="""\
Cloud sync mirrors your local data to a shared Google Sheet so
coordinators can view it remotely.

## Prerequisites

- A Google service account with credentials JSON file
- A target Google Sheet, with the service account email shared as an
  Editor

## To connect

1. **Settings → Cloud Sync**
2. Click **Load Credentials** — select the service account JSON file
3. **Spreadsheet ID** — paste the spreadsheet's ID (the long string
   in the Sheet's URL)
4. Click **Save Sync Settings**
5. The app verifies access and reports success or error

After save, the **Sync to Cloud** button appears in the title bar and
the sync indicator becomes visible.

## Per-tab toggles

You can choose which tabs to sync:

- **Required** (always synced): Vendor Reimbursement, Detailed Ledger,
  Error Log, Agent Tracker, Geolocation, FMNP Entries
- **Optional** (off by default): FAM Match Report, Transaction Log,
  Activity Log, Market Day Summary

Optional tabs are heavier — turn on only if your coordinator needs
them remotely.

## Authoritative data

The local SQLite database is **authoritative**.  Sync is a one-way
mirror.  Editing the Google Sheet does not push changes back into the
app.
""",
        keywords=('google sheets', 'sync', 'cloud', 'connect', 'credentials'),
        related_articles=('connect-drive', 'sync-overview', 'sync-indicator'),
        screen='settings',
    ),

    Article(
        id='connect-drive',
        category_id='settings',
        title='Connecting Google Drive for photo upload',
        body="""\
Drive upload is configured automatically once Google Sheets sync is
connected — they share the same service account credentials.

## Where photos go

Each photo is uploaded to a Drive folder named after your market /
date / entry.  The folder structure:

```
FAM Market Manager Photos /
  └── {Market Name} /
       └── {YYYY-MM-DD} /
            └── {entry_id}_{vendor}_FMNP.jpg
```

Filenames are deterministic so re-uploading the same photo (after
accidental Drive deletion) restores the same name.

## Folder permissions

The service account creates the folder.  Photos inherit the parent
folder's permissions.  By default that means only the service account
can see them — share the folder with coordinators if you want them
to view photos directly in Drive.

## Storage quota

The service account has its default Drive quota (typically 15 GB).
For heavy-FMNP markets uploading hundreds of photos per market day,
quota usage builds up — see *Where my data lives* for monitoring tips.

## What if Drive is offline

Photos stay in the local `photos/` folder and queue for upload.  The
next time the laptop has internet, the next sync uploads them.  No
photos are lost.
""",
        keywords=('drive', 'photo', 'upload', 'cloud', 'storage'),
        related_articles=('connect-sheets', 'fmnp-photo', 'photo-upload-pending'),
        screen='settings',
    ),

    # ── Sync & Cloud ──────────────────────────────────────────────

    Article(
        id='sync-overview',
        category_id='sync',
        title='What gets synced to Google Sheets',
        body="""\
Cloud sync produces a real-time mirror of your local data in the
configured Google Sheet.

## When sync runs

Sync is triggered automatically by:

- Confirming a payment (transaction confirmation)
- Saving a payment as draft
- Adding, editing, or deleting an FMNP entry
- Saving an adjustment in the Adjustments screen
- Voiding a transaction
- Voiding a customer order
- Closing a market day
- Every 5 minutes during an open market day (periodic timer)

Multiple triggers within 60 seconds are debounced into a single sync
to avoid flooding the Google Sheets API.

You can also click **Sync to Cloud** in the header bar to force sync.

## What's synced

| Sheet tab | What's there |
|---|---|
| Detailed Ledger | One row per transaction (and per FMNP entry) |
| Vendor Reimbursement | Per-vendor totals for the period |
| FAM Match Report | Per-payment-method match dollars |
| FMNP Entries | One row per FMNP entry from the FMNP Entry screen |
| Geolocation | Per-zip-code transaction counts |
| Activity Log | Most recent 500 audit log entries |
| Market Day Summary | Per-market-day totals |
| Error Log | Most recent application errors |
| Transaction Log | Most recent 500 transactions |
| Agent Tracker | One row per device — last sync time + status |

## Identity columns

Every row has `market_code` and `device_id` columns.  Those let
multi-laptop deployments coexist in one shared Sheet — each laptop
upserts only its own rows.
""",
        keywords=('sync', 'cloud', 'google sheets', 'mirror', 'tabs'),
        related_articles=('connect-sheets', 'sync-indicator', 'sync-failed'),
    ),

    Article(
        id='sync-indicator',
        category_id='sync',
        title='Sync indicator states explained',
        body="""\
The colored dot in the header bar (next to the Sync to Cloud button)
reflects the current state of cloud sync.  It does **not** make a
live internet speed test — it reports what the app knows for certain.

## States

| Color | Label | Meaning |
|---|---|---|
| 🟢 Green | **Last sync OK** | Most recent sync attempt succeeded |
| 🔴 Red | **Sync failed** | Most recent sync attempt hit an error |
| 🟡 Amber | **Syncing…** | A sync is currently running |
| 🟡 Amber | **Attention** | Last sync OK but one or more photos had issues |
| ⚫ Gray | **No network** | Windows reports the laptop is disconnected |
| ⚫ Gray | **Not synced yet** | Sync configured but no sync has run yet |
| (hidden) | — | Sync not configured |

## "No network" — your data is safe

If you see "No network", your data is **stored locally** on this
laptop and will sync automatically the next time the laptop reconnects
and a sync runs.  You won't lose anything.

The detail line includes "data safe locally" specifically to reassure
you.

## How "No network" is detected

The app uses Windows' built-in network reachability service.  It
detects connection state without making any outbound probes.  If
Windows says you're disconnected, the indicator says so.
""",
        keywords=('indicator', 'sync', 'green', 'red', 'gray', 'amber',
                  'no network', 'online', 'offline'),
        related_articles=('sync-overview', 'sync-failed', 'offline-operation'),
    ),

    Article(
        id='sync-failed',
        category_id='sync',
        title='Sync indicator is red ("Sync failed") — what to do',
        body="""\
A red **Sync failed** indicator means the most recent sync attempt
encountered an error.  Your local data is safe — sync just couldn't
push to Google Sheets right now.

## Quick checks

1. **Internet connection** — open a browser, try loading google.com
2. **Click Sync to Cloud** to manually retry — sometimes a transient
   network blip resolves immediately
3. **Hover the indicator** — the tooltip shows the specific error
   (e.g. "Sheet errors: Transactions" or a specific tab)

## Common causes and fixes

| Tooltip says | Likely cause | Fix |
|---|---|---|
| Network error | Wi-Fi flaky | Retry, or wait until reconnected |
| Auth error | Service account expired / removed | Re-load credentials in Settings |
| Quota exceeded | Hit Google API rate limit | Wait 60 seconds, retry |
| Spreadsheet not found | Sheet was deleted or moved | Verify Spreadsheet ID in Settings |
| Permission denied | Service account no longer has Editor access | Re-share the Sheet with the service account email |

## If sync stays red

1. Note the time and the tooltip message
2. Check `%APPDATA%\\FAM Market Manager\\fam_manager.log` — search
   for "Sync" — the most recent error has full details
3. Use the **Help → System Status → Copy Diagnostic Info** button to
   gather everything in one paste, then send to your coordinator

In the meantime, your local data is fine.  The next successful sync
will push everything that was missed.
""",
        keywords=('sync', 'failed', 'red', 'error', 'fix', 'troubleshoot'),
        related_articles=('sync-indicator', 'sync-overview', 'connect-sheets'),
    ),

    Article(
        id='photo-upload-pending',
        category_id='sync',
        title='Photo upload behavior',
        body="""\
Photos are uploaded to Google Drive as part of the sync process.
They have their own queue separate from the Sheets sync.

## Upload sequence

1. Photo attached locally → resized, stored in `photos/`
2. Next sync → checks for photos with no Drive URL → uploads them
3. After upload → Drive URL written to the database (so reports can
   link to the file)

## "Pending photos"

The sync indicator's **Attention** state means the Sheets sync
succeeded but some photos failed to upload (Drive auth issue, quota,
or transient network problem).  Click the indicator to see the
tooltip with details.

## Verification cycle

Roughly every 10 minutes, the sync checks that uploaded photos still
exist in Drive (a coordinator might have accidentally deleted one).
Confirmed-missing photos are queued for re-upload automatically.

The verification is **conservative** — a transient network error
during verification does NOT cause a re-upload.  Only confirmed
deletions / 404s trigger re-upload.

## Photo dedup

Photos are deduplicated by **SHA-256 content hash**.  If the same
image is attached to multiple receipts, only one Drive file is
created.  Re-uploading after an accidental delete restores the same
hash → same file.

## When uploads fail

Failed uploads are retried on the next sync.  After 5 consecutive
failed attempts for the same photo, it's logged but not retried until
the next app launch.  Check the Error Log tab in Reports for
persistent failures.
""",
        keywords=('photo', 'upload', 'drive', 'pending', 'failed', 're-upload'),
        related_articles=('connect-drive', 'fmnp-photo', 'sync-failed'),
    ),

    Article(
        id='no-network-data-safe',
        category_id='sync',
        title='"No network" — your data is safe locally',
        body="""\
If the sync indicator shows **No network**, the laptop is
disconnected from the internet.  This is not a data problem — it's
a sync problem.

## What's happening

- Windows is reporting that no network interface is reachable
- The app cannot push to Google Sheets or upload photos right now
- The app continues to work fully — record receipts, FMNP entries,
  and adjust transactions normally

## What's NOT happening

- **No data is lost.**  The local SQLite database is the
  authoritative source of truth.
- **Backups still run.**  The 5-minute auto-backup timer continues
  while a market day is open.
- **The ledger backup file still updates** after each transaction.
- **Reports still work.**  All reports show the most current local
  data.

## When the network returns

- The indicator flips back to green automatically
- Click **Sync to Cloud** (or wait for the next periodic 5-minute
  sync) to push the data that accumulated while offline
- Photos queued for upload are uploaded in the same sync batch

## Long-running offline sessions

Multi-day offline operation is fine.  At end-of-day, the local
ledger backup (`fam_ledger_backup.txt`) is human-readable — you can
open it in any text editor as proof of the day's transactions.
""",
        keywords=('no network', 'offline', 'disconnected', 'safe', 'local'),
        related_articles=('sync-indicator', 'offline-operation', 'where-data-lives'),
    ),

    # ── Updates & Maintenance ─────────────────────────────────────

    Article(
        id='check-for-updates',
        category_id='maintenance',
        title='Checking for updates',
        body="""\
**Settings → Application Updates → Check for Updates**

The app queries GitHub Releases for the latest version.  If a newer
version is available, you'll see:

- The new version number
- Release notes summarizing what changed
- A **Download and Install** button

## What happens when you click Download and Install

1. The new version's signed zip downloads to `%APPDATA%\\FAM Market
   Manager\\_update_download\\`
2. The app prepares an update batch script
3. The app records a **pending-update marker** with the target version
4. The app exits
5. The batch script:
   - Backs up your current `FAM Manager` folder
   - Extracts the new version over the old one
   - Restarts the app
6. The app launches at the new version
7. The pending-update marker is checked — if the running version
   matches the marker, success is logged.  If not, a warning dialog
   appears.

## Auto-check

By default, the app checks for updates 5 seconds after launch.  You
can disable this in **Settings → Application Updates** by unchecking
**Check for updates automatically on startup**.

## If auto-update fails

For laptops on v1.9.5 or earlier, auto-update has a known TLS or
nested-zip defect.  Manually download from
https://github.com/seansaball/fam-market-manager/releases and replace
the `FAM Manager` folder.  All versions from v1.9.6 forward auto-update
reliably.
""",
        keywords=('update', 'upgrade', 'new version', 'install'),
        related_articles=('manual-install', 'where-data-lives'),
        screen='settings',
    ),

    Article(
        id='manual-install',
        category_id='maintenance',
        title='Manually installing a new version',
        body="""\
If auto-update doesn't work or you prefer to install manually:

1. Go to https://github.com/seansaball/fam-market-manager/releases
2. Download `FAM_Manager_v{X.Y.Z}.zip` for the version you want
3. Close FAM Manager (File menu → Exit, or click the X)
4. Extract the zip — it produces a `FAM Manager` folder
5. Find your existing `FAM Manager` folder (wherever you installed it,
   typically your Desktop or `C:\\Program Files\\`)
6. Replace it with the extracted folder.  Drag-and-drop, or copy +
   paste with overwrite.
7. Launch the new version

Your data is **NOT** in the `FAM Manager` folder — it's in
`%APPDATA%\\FAM Market Manager\\`, which is untouched by the
replacement.  All transactions, settings, photos, and backups are
preserved.

## When manual install is required

- v1.9.5 or earlier (auto-update was buggy)
- Air-gapped laptops without internet
- Recovering from a botched auto-update
- Going back to an earlier version

## Going back to an earlier version

Same process — download the older zip, replace the folder.  The
database is forward-compatible with older app versions only at the
schema level — if a newer version added a schema change, you'd need to
restore from a pre-migration backup.  Don't downgrade across schema
versions in production without coordinating with engineering.
""",
        keywords=('manual', 'install', 'download', 'upgrade', 'replace'),
        related_articles=('check-for-updates', 'where-data-lives', 'backups'),
    ),

    Article(
        id='backups',
        category_id='maintenance',
        title='Database backups',
        body="""\
The app keeps automatic backups of the database for recovery in case
of corruption or accidental data loss.

## Where backups go

`%APPDATA%\\FAM Market Manager\\backups\\`

## When backups happen

- **Market day open** — labeled `market_open`
- **Market day close** — labeled `market_close`
- **Every 5 minutes during an open market day** — labeled `auto`
- **Manually** via Settings (rare)

Each backup uses SQLite's online backup API for a clean snapshot.  The
WAL (write-ahead log) is correctly captured.

## Retention

The 20 most recent backups are kept.  Older ones are deleted
automatically.

For a 6-hour market day with 5-minute intervals, the auto-backups
quickly fill the retention list — the market_open backup may be rolled
out after about 100 minutes.  If you need to preserve a specific
backup long-term, copy it out of the `backups/` folder before the
next market day.

## Restoring

This is an engineering operation.  Don't attempt without coordinator
support:

1. Close FAM Manager
2. Rename `fam_data.db` to `fam_data.db.broken`
3. Copy the desired backup into the same folder, renaming it to
   `fam_data.db`
4. Launch the app

The schema_version table will reflect the version of the backup —
make sure your app version is compatible.
""",
        keywords=('backup', 'restore', 'recovery', 'data', 'corruption'),
        related_articles=('where-data-lives', 'manual-install'),
    ),

    Article(
        id='where-data-lives',
        category_id='maintenance',
        title='Where my data lives',
        body="""\
All persistent FAM Manager data lives in a single Windows folder:

```
%APPDATA%\\FAM Market Manager\\
```

(That resolves to something like
`C:\\Users\\<username>\\AppData\\Roaming\\FAM Market Manager\\`.)

## What's in there

| File / folder | What it is |
|---|---|
| `fam_data.db` | The main SQLite database — every transaction, FMNP entry, etc. |
| `fam_data.db-wal` | SQLite write-ahead log — temporary, auto-managed |
| `fam_manager.log` | Application log (rotated, max 20 MB total) |
| `fam_ledger_backup.txt` | Human-readable text mirror of every transaction |
| `backups/` | Database snapshots (20 most recent) |
| `photos/` | Local copies of all attached photos |
| `_update_download/` | Auto-update zip download (transient) |
| `_update_temp/` | Auto-update extraction temp dir (transient) |
| `_update_backup/` | Previous app version, kept after auto-update |
| `_pending_update.json` | Auto-update marker file (transient) |
| `_fam_update.log` | Most recent auto-update batch script output |

## Backing up the entire data directory

For long-term archives (off-season, end-of-year):

1. Close FAM Manager
2. Copy the entire `%APPDATA%\\FAM Market Manager\\` folder to an
   external drive, NAS, or cloud storage
3. Done — that copy can be restored to any laptop later by reversing
   the copy

The application folder (where `FAM Manager.exe` lives) is **separate**
from the data folder.  Replacing the application folder during an
upgrade does not touch the data folder.

## Approximate sizes

After a year of heavy use:

- `fam_data.db` — typically under 30 MB
- `photos/` — depends on FMNP volume, can grow to 1-2 GB at heavy
  markets
- `backups/` — bounded at ~3 GB worst case (20 × ~150 MB each)
- `fam_manager.log` — bounded at 20 MB (rotation)

Total typical: well under 5 GB.
""",
        keywords=('data', 'location', 'folder', 'appdata', 'where', 'files'),
        related_articles=('backups', 'manual-install'),
    ),
)


# ══════════════════════════════════════════════════════════════════
#   TROUBLESHOOTING FLOWS
# ══════════════════════════════════════════════════════════════════

TROUBLESHOOTING_FLOWS: tuple[TroubleshootingFlow, ...] = (

    TroubleshootingFlow(
        id='ts-sync-red',
        title='The sync indicator is red',
        symptom='Header bar shows "Sync failed"',
        steps=(
            "1. Hover the indicator — note the tooltip's specific error",
            "2. Open a browser and load google.com — confirm internet works",
            "3. If internet is down, wait until reconnected; sync will recover automatically",
            "4. If internet is up, click 'Sync to Cloud' to manually retry",
            "5. If still red, check Settings → Cloud Sync — confirm Spreadsheet ID is set and Credentials show Loaded",
            "6. If permission error: re-share the Google Sheet with the service account email shown in Settings",
            "7. Still failing: open Help → System Status → Copy Diagnostic Info → send to your coordinator",
        ),
        keywords=('sync', 'red', 'failed', 'error', 'troubleshoot'),
        related_articles=('sync-failed', 'sync-indicator', 'connect-sheets'),
    ),

    TroubleshootingFlow(
        id='ts-no-network',
        title='Sync indicator says "No network"',
        symptom='Gray dot, "No network" label',
        steps=(
            "1. This is not a data problem — your local data is safe and the app is fully functional offline",
            "2. Check your laptop's Wi-Fi icon (system tray) — is Windows showing connected?",
            "3. If disconnected, reconnect to Wi-Fi normally",
            "4. The indicator will flip to green automatically within a second of reconnecting",
            "5. If you've been offline a while, click 'Sync to Cloud' to push the queued data immediately",
            "6. If Windows says connected but the indicator stays gray, restart the app",
        ),
        keywords=('no network', 'offline', 'disconnected', 'wifi'),
        related_articles=('no-network-data-safe', 'sync-indicator'),
    ),

    TroubleshootingFlow(
        id='ts-photo-not-uploading',
        title='A photo isn\'t uploading to Drive',
        symptom='Sync indicator shows Attention, or photo is missing in Drive',
        steps=(
            "1. Confirm Drive is connected — Settings → Cloud Sync → Drive section",
            "2. Confirm the service account has access — open the Drive folder in your browser, you should see other photos",
            "3. Click 'Sync to Cloud' to retry — sometimes a transient network error",
            "4. Wait 10 minutes — the verification cycle runs every 10 minutes and will queue confirmed-missing photos for re-upload",
            "5. Check Reports → Error Log for 'Drive' entries with details",
            "6. If the service account quota is full (15 GB default), uploads will fail silently — check the Drive folder size",
            "7. Persistent failure: copy the diagnostic info from Help → System Status, send to coordinator",
        ),
        keywords=('photo', 'drive', 'upload', 'failed', 'missing'),
        related_articles=('photo-upload-pending', 'connect-drive', 'sync-failed'),
    ),

    TroubleshootingFlow(
        id='ts-cant-find-transaction',
        title='Can\'t find a specific transaction',
        symptom='You know it was entered but can\'t see it on the Adjustments screen',
        steps=(
            "1. Adjustments screen — clear the date filter (set to All time) and click Search",
            "2. Try filtering by the FAM transaction ID prefix (e.g. 'FAM-BFM-' for Bethel Park, found on the receipt)",
            "3. Try filtering by status — if it was Voided, the default filter may exclude it",
            "4. Check the Audit Log panel at the bottom — search for the transaction's record_id or related actions",
            "5. Verify the market day was opened on the date you think — Market screen shows all market days",
            "6. If the transaction was deleted or never confirmed, look for it in Receipt Intake → Pending Orders",
            "7. As a last resort, open `fam_ledger_backup.txt` in a text editor and search for the customer label or amount",
        ),
        keywords=('find', 'missing', 'transaction', 'search', 'lost'),
        related_articles=('audit-log', 'adjust-transaction', 'where-data-lives'),
    ),

    TroubleshootingFlow(
        id='ts-cap-warning-wrong',
        title='Match cap warning when I shouldn\'t have one',
        symptom='Payment Screen shows the customer is over their cap, but you don\'t think they should be',
        steps=(
            "1. Verify it's the correct customer in the dropdown — Returning Customer label may have been picked by mistake",
            "2. Click the Returning Customer dropdown — it shows the prior match used by each customer label",
            "3. If the wrong prior match is shown, an earlier transaction may have been mis-labeled",
            "4. Confirm the market day's Match Limit setting — Settings → Markets → edit. Did the limit get changed today?",
            "5. Check the customer's prior transactions — Reports → Detailed Ledger, filter by customer label",
            "6. If a previous transaction has the wrong customer label, fix it via Adjustments — change the customer order linkage",
            "7. The 1-cent overshoot ($100.01 instead of $100) on cap is acceptable — that's the penny reconciliation rounding behavior",
        ),
        keywords=('cap', 'limit', 'wrong', 'exceeded', 'warning'),
        related_articles=('match-cap', 'returning-customer', 'penny-reconciliation'),
    ),

    TroubleshootingFlow(
        id='ts-app-slow',
        title='App is slow or unresponsive',
        symptom='Buttons take a long time to respond, or the screen feels frozen',
        steps=(
            "1. Wait 10 seconds — most stalls are a sync running in the background and will clear",
            "2. Check the sync indicator — if it shows Syncing…, the app is working, just busy",
            "3. Check available RAM — close Chrome tabs / other apps if you're under 1 GB free",
            "4. Restart the app — File menu → Exit, then re-open. Database state is preserved",
            "5. If the app won't close, end the FAM Manager.exe process via Task Manager (Ctrl+Shift+Esc) — your data is safe; the auto-backup ran within the last 5 minutes",
            "6. Restart the laptop if symptoms persist after restart",
            "7. Check `%APPDATA%\\FAM Market Manager\\fam_manager.log` for repeated errors — paste recent ERROR-level lines into a coordinator note",
        ),
        keywords=('slow', 'unresponsive', 'frozen', 'hang', 'stuck'),
        related_articles=('backups', 'where-data-lives'),
    ),

    TroubleshootingFlow(
        id='ts-app-wont-start',
        title='App won\'t start after an update',
        symptom='Double-clicking FAM Manager.exe does nothing, or window flashes and closes',
        steps=(
            "1. Wait 30 seconds — Windows Defender may be scanning the new exe",
            "2. Right-click `FAM Manager.exe` → Properties → Unblock (if visible)",
            "3. Try running as Administrator (right-click → Run as administrator)",
            "4. Check if a previous instance is stuck — open Task Manager, look for FAM Manager.exe, end it",
            "5. The single-instance mutex prevents two copies running. If the previous instance crashed, restart Windows to clear",
            "6. If still failing, the auto-update may have left a partial install. Manually re-download the zip from GitHub Releases and overwrite the FAM Manager folder",
            "7. Your data in %APPDATA%\\FAM Market Manager\\ is preserved through any of these steps",
        ),
        keywords=('start', 'launch', 'open', 'crash', 'won\'t', 'broken'),
        related_articles=('manual-install', 'where-data-lives', 'check-for-updates'),
    ),

    TroubleshootingFlow(
        id='ts-match-wrong',
        title='Match calculation looks wrong',
        symptom='The FAM match on a payment row doesn\'t match what you expected',
        steps=(
            "1. Check the payment method's match % — Settings → Payment Methods, click the row to see configured percent",
            "2. Verify the Charge field — match is calculated as Charge × Match% / (100 + Match%)",
            "3. If the customer is at or near their daily cap, the match may have been reduced by cap clamping — check the cap remaining at top of Payment Screen",
            "4. A 1-cent difference between expected and shown match is normal (penny reconciliation)",
            "5. Review the customer's prior transactions — Reports → Detailed Ledger filtered by customer label — to confirm cap accumulation is correct",
            "6. Run the simulation script (engineering tool) to verify the formula is working — see Help → System Status → Copy Diagnostic Info to gather context",
        ),
        keywords=('match', 'wrong', 'incorrect', 'calculation', 'percent'),
        related_articles=('match-cap', 'penny-reconciliation', 'add-payment-method'),
    ),

    TroubleshootingFlow(
        id='ts-receipt-photo-missing',
        title='I attached a photo but it\'s not showing',
        symptom='Photo was attached during Receipt Intake or FMNP Entry but doesn\'t appear in reports',
        steps=(
            "1. Confirm the photo was actually saved — re-open the transaction in Adjustments / re-open the FMNP entry. Does the photo slot show the image?",
            "2. If the slot is empty, the photo wasn't saved (probable cause: hit Cancel before save). Re-attach and save again.",
            "3. If the slot shows the photo locally but not in Drive, see 'A photo isn\\'t uploading to Drive'",
            "4. If reports show no photo but the entry has one: filter the Detailed Ledger to that transaction — the photo URL column shows the Drive link if uploaded",
            "5. Check `%APPDATA%\\FAM Market Manager\\photos\\` — the file should be there with naming like `fmnp_{entry_id}_*.jpg`",
            "6. If the local file is missing, the photo was lost during save (rare). Re-attach if you have a copy.",
        ),
        keywords=('photo', 'missing', 'attached', 'gone', 'lost'),
        related_articles=('fmnp-photo', 'photo-upload-pending', 'where-data-lives'),
    ),

    TroubleshootingFlow(
        id='ts-fmnp-doesnt-show',
        title='FMNP doesn\'t show as a payment option',
        symptom='Trying to add FMNP as a payment row on the Payment Screen but it\'s not in the dropdown',
        steps=(
            "1. As of v1.9.8, FMNP is INACTIVE by default. This is intentional — most markets use only the FMNP Entry screen.",
            "2. To make FMNP available on the Payment Screen: Settings → Payment Methods → find FMNP → click Activate",
            "3. Return to the Payment Screen — FMNP now appears in the method dropdown",
            "4. The FMNP Entry screen is unaffected by this toggle and works regardless",
            "5. If FMNP still doesn\\'t appear after activating, verify the current market has FMNP assigned — Settings → Markets → edit market → Payment Methods tab",
        ),
        keywords=('fmnp', 'missing', 'not showing', 'payment screen', 'option'),
        related_articles=('fmnp-activate-payment', 'fmnp-overview', 'add-payment-method'),
    ),
)


# ══════════════════════════════════════════════════════════════════
#   ACCESSORS
# ══════════════════════════════════════════════════════════════════

def get_category(category_id: str) -> Optional[Category]:
    """Return the Category with the given id, or None if not found."""
    for c in CATEGORIES:
        if c.id == category_id:
            return c
    return None


def get_article(article_id: str) -> Optional[Article]:
    """Return the Article with the given id, or None if not found."""
    for a in ARTICLES:
        if a.id == article_id:
            return a
    return None


def get_articles_by_category(category_id: str) -> tuple[Article, ...]:
    """Return all articles for a given category, preserving definition order."""
    return tuple(a for a in ARTICLES if a.category_id == category_id)


def get_troubleshooting_flow(flow_id: str) -> Optional[TroubleshootingFlow]:
    """Return the TroubleshootingFlow with the given id, or None."""
    for t in TROUBLESHOOTING_FLOWS:
        if t.id == flow_id:
            return t
    return None
