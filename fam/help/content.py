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
2. **Non-denominated rows that are NOT user-locked are reset to zero**,
   then filled as "absorbers."  Cash, SNAP, and other non-denominated
   methods that have their per-row ⚡ icon GREEN (Active) get the
   remainder of the receipt total.
3. **User-locked rows (grey ⚡) are skipped.**  Auto-Distribute respects
   any value the volunteer has explicitly typed or pinned via the
   ⚡ toggle.  See `auto-distribute-toggle` for full details.
4. **The match percentage is honored**: SNAP at 100% match means the
   customer pays half and the FAM match covers the other half.
5. **The match cap is honored** if active.  If the customer would
   exceed their daily $100 cap, charges on Active rows are increased
   so the customer covers the deficit.

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
        related_articles=('split-payment', 'match-cap', 'penny-reconciliation', 'auto-distribute-toggle'),
        screen='payment',
    ),

    Article(
        id='auto-distribute-toggle',
        category_id='during-market',
        title='The per-row ⚡ toggle (Active vs Locked)',
        body="""\
Each non-denominated payment row (SNAP, Cash, etc.) has a small
**⚡ icon** next to the amount field.  It controls whether that row
participates in **Auto-Distribute**.

## Two states

- **Green ⚡ (Active)** — Auto-Distribute will fill or refill this
  row.  The row is the "overflow target" that absorbs the receipt
  remainder when you click Auto-Distribute.
- **Grey ⚡ (Locked)** — Auto-Distribute will skip this row.  The
  volunteer's typed value stays exactly as entered, even when the
  daily FAM match cap kicks in.

## How rows transition

- **Typing into the amount field auto-locks the row.**  As soon as
  you type a value (e.g. "$125"), the icon flips to grey.  This is
  the default "I know exactly how much SNAP the customer has" case.
- **Adding a row when one is already Active defaults the new row to
  Locked.**  Only one non-denom row at a time can be the overflow
  target — if you add a third method, it comes in Locked at $0 and
  you can either type a value or click ⚡ to activate it.
- **Click a grey ⚡** to activate the row.  The previously-Active
  row automatically locks, so there's still exactly one overflow
  target.
- **Click a green ⚡** to lock the current value (pin it where it is).

## When to use each state

- **Customer has a fixed amount on one method, rest in cash**: type
  SNAP $125 (auto-locks), let the green ⚡ Cash row absorb the rest
  via Auto-Distribute.
- **Volunteer wants to manually balance everything**: lock all rows
  by typing values; the engine respects every typed amount.
- **Customer wants to maximize FAM match without specifying amounts**:
  leave one row green (Active) and click Auto-Distribute — the engine
  fills the green row with whatever absorbs the receipt.

## Why this exists

Pre-v2.0.7, Auto-Distribute would silently inflate any row's value
if the daily match cap shrank the FAM contribution.  The volunteer
would type "$125 SNAP" (because that's all the customer has on their
EBT card), click Auto-Distribute, and see SNAP magically become
"$138.09" — confusing and unfixable without deleting the row.  The
⚡ toggle makes intent explicit and gives volunteers a clear way to
say "this value is final."

## Auto-Distribute appears to do nothing

Check the ⚡ icon on every non-denom row.  If they're all grey
(Locked), there's no Active overflow target for Auto-Distribute to
fill.  Click one row's grey ⚡ to release it, then click
Auto-Distribute again.
""",
        keywords=('toggle', 'lock', 'unlock', 'overflow', 'active', 'locked', 'green', 'grey', 'gray', 'lightning', 'icon', 'pin'),
        related_articles=('auto-distribute-button', 'split-payment', 'match-cap'),
        screen='payment',
    ),

    Article(
        id='customer-forfeit',
        category_id='during-market',
        title='Customer Forfeit (token-value over-tender)',
        body="""\
The **Customer Forfeit** summary card and report column track money
the customer over-tendered when handing a denominated payment unit
(Food RX, Food Bucks, FMNP) for a receipt smaller than the unit's
face value.

## Example

Customer hands a $10 Food RX token to a vendor with a $1.45
receipt.  The full $10 leaves the customer's pocket, but only
$1.45 reaches the vendor.  The remaining $8.55 is the
**customer forfeit** — over-tender that didn't apply.

The Payment Screen shows:

| Card | Value |
|---|---|
| Receipt Total | $1.45 |
| Customer Pays | $10.00 |
| FAM Match | $0.00 |
| **Customer Forfeit** | **$8.55** |

## Why we track it

- Reports show the customer's **physical handout** in the Food RX
  column ($10), not the post-forfeit amount ($1.45).  Volunteers
  can reconcile the report against what actually came out of the
  customer's pocket.
- The vendor still gets exactly the receipt total ($1.45) — Phase
  B forfeit doesn't shift money to the vendor.
- The audit trail records the unaccounted $8.55 so a future
  reviewer can see "the customer over-tendered, not a bug."

## When forfeit fires

- **Phase A (silent)**: FAM match contribution is reduced first to
  absorb the gap.  No forfeit recorded.
- **Phase B (visible)**: if Phase A can't cover the full overage
  (e.g. there's no match available because the cap is already at
  $0), the customer-side `customer_charged` is reduced AND the
  forfeit amount is recorded.

In normal use the Customer Forfeit card shows $0.00.  It only
goes non-zero when a denominated unit's face value exceeded what
the receipt and the FAM match could absorb.
""",
        keywords=('forfeit', 'token', 'over-tender', 'overage', 'denomination', 'phase b', 'unaccounted'),
        related_articles=('match-cap', 'auto-distribute-toggle'),
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
        related_articles=('match-cap', 'returning-customer', 'adjust-transaction',
                          'split-orders-when-stuck'),
        screen='payment',
    ),

    Article(
        id='split-orders-when-stuck',
        category_id='corrections',
        title='When the Payment screen blocks you — split into separate orders',
        body="""\
If you hit a hard block on the Payment screen — a "Payment row mismatch"
warning, a per-vendor over-allocation error, or any other dialog that
refuses to let you confirm — and Auto-Distribute does not fix it after
one or two clicks, **the simplest, safest resolution is to break the
customer's receipts out into separate orders, one payment method per
order.**

This is always the right answer when the math doesn't reconcile and
nothing in the troubleshooting flows fits.  It is also the recommended
resolution when the dialog explicitly suggests splitting (the daily
FAM match cap is binding and no single combination will balance —
each smaller order gets its own clean cap allocation).

## The split-order workflow

1. **Cancel** out of the Payment screen (do not Confirm).
2. If you started from a Pending Order, return to **Receipt Intake**
   and **Discard** the in-progress order.  The receipts you typed are
   reusable in Step 3.
3. In **Receipt Intake**, create a new customer order for the
   **same customer label** (returning-customer dropdown, or type the
   label).
4. Add only the receipts that one payment method will cover (e.g.
   the Food RX portion).
5. Go to **Payment**, enter only that one method, Confirm.
6. Repeat from Step 3 for the next payment method (e.g. SNAP for
   the rest of the receipts).

Because the customer label is the same, the cap accounting carries
through automatically — the second order sees the first order's match
already used.  Reports group by customer label, so the customer's day
still rolls up to one row per category.  Nothing is lost.

## Why this works

The payment engine is designed so each order independently reconciles
its receipts against its payments and its share of the daily cap.
When a single order tries to cram an awkward combination of methods
under a tight cap, the engine can produce a state where there is no
clean balance — denominated tokens have fixed face values, the cap
limits how much FAM can absorb, and the receipt total is what it is.
Two simpler orders sidestep the awkward intersection entirely.

## When NOT to split

If Auto-Distribute fixes the dialog on the first click, you do not
need to split.  Just Confirm and continue.

If the block is something other than a math mismatch — a missing
photo, an ineligible-vendor warning, an "Already running" dialog —
splitting will not help.  See the matching troubleshooting flow under
**Help → Troubleshooting** instead.

## See also

- **Help → Troubleshooting → "Hard block on the Payment screen"** for
  a step-by-step decision flow.
- **`returning-customer`** article for how the same-label trick keeps
  cap accounting clean across multiple orders.
""",
        keywords=('split', 'stuck', 'blocked', 'mismatch', 'cap',
                  'cannot confirm', 'separate orders', 'workaround',
                  'breakup', 'multiple orders'),
        related_articles=('match-cap', 'cap-warning', 'returning-customer',
                          'split-payment', 'auto-distribute-button',
                          'adjust-transaction'),
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
                          'fmnp-activate-payment',
                          'fmnp-all-market-days'),
    ),

    Article(
        id='fmnp-all-market-days',
        category_id='fmnp',
        title='FMNP "All Market Days" filter — and why Save is greyed out',
        body="""\
The FMNP Check Tracking page's market-day dropdown defaults to
**"All Market Days"**, which is a **browse-only filter** for
searching the full FMNP entry history.

## What it shows

When "All Market Days" is selected:

- The entries table shows FMNP entries from **every market day**,
  not just one.  Combined with the date-range filter on the same
  screen, you can search for "all FMNP entries between June 1 and
  June 15" or "all entries for vendor X across the season."
- The **Market Day** column in the table identifies which date
  each entry came from.
- The **"Add FMNP Entry"** button greys out and an inline hint
  appears next to it: *"← Pick a specific market day above to add
  a new entry."*

## Why Save greys out

You can't attribute a new FMNP entry to "all markets" — every
entry needs a single concrete market day so the date links to a
real Open or Closed market_day record.  Selecting "All Market
Days" puts the form in **browse mode** — you can search and edit
existing entries, but adding a new one requires picking a
specific date first.

## To add a new entry

1. Pick a specific market day from the dropdown (the entries
   table will filter to just that day)
2. The Save button enables and the inline hint disappears
3. Fill in the vendor, dollar amount, and other fields
4. Click **Add FMNP Entry**

## To search the full history

1. Leave "All Market Days" selected
2. Use the **date range** filter to narrow by date span
3. Use **Edit** / **Delete** buttons in the table to modify
   existing entries
""",
        keywords=('fmnp', 'all market days', 'browse', 'filter',
                  'save', 'greyed out', 'disabled', 'date range'),
        related_articles=('fmnp-overview', 'fmnp-via-tracking'),
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

## Setting eligible payment methods (v1.9.9+)

Click the **Methods** button on the vendor row to choose which payment
methods this vendor is eligible to accept.  This is required for
**denominated** instruments like Food Bucks or FMNP-as-payment that
bind to one specific vendor when the customer hands them over.

Example: if Food Bucks are only valid at produce stalls, check Food
Bucks for produce vendors and leave it unchecked for bakeries — when
a volunteer enters Food Bucks on the Payment screen the per-row
vendor dropdown will only list the eligible produce vendors.

By default every vendor is registered for every method (so existing
flows keep working); use the dialog to tighten this to your market's
real rules.

## Editing

Pencil icon on the vendor row.  Most fields editable.  Renaming a
vendor preserves historical reports (snapshots).

## Deactivating

Toggle the vendor's active state to remove them from new transaction
options without losing history.  Inactive vendors don't appear in
Receipt Intake but their historical transactions remain in reports.
""",
        keywords=('vendor', 'add', 'create', 'new', 'farmer',
                  'eligible', 'methods', 'food bucks', 'denominated'),
        related_articles=('add-market', 'add-payment-method',
                           'denominated-payment-vendor-binding'),
        screen='settings',
    ),

    Article(
        id='denominated-payment-vendor-binding',
        category_id='during-market',
        title='Denominated payments and the vendor dropdown',
        body="""\
**Why a payment row sometimes asks "which vendor?"**

Denominated payment methods like **Food Bucks** and **FMNP** (when
configured as a payment method) are physical paper instruments — a
$5 Food Bucks check is one specific piece of paper handed to one
specific vendor.  Unlike SNAP or Cash (which are aggregate sums of
money), a denominated instrument can't be "spread" across vendors.

When a customer's order has multiple vendors AND the payment method
is denominated, the row shows an inline **vendor dropdown** between
the method and the charge field.  Pick the vendor that received this
particular instrument.

## What appears in the dropdown

Only vendors that meet **both** of these conditions:

1. They appear on the current customer's order (they have a receipt)
2. They are registered for this method via **Settings → Vendors →
   Methods** (the eligibility checklist)

If the dropdown is empty, the customer's chosen instrument isn't
accepted by anyone on this order — verify the eligibility config or
have the customer pay another way.

## Multiple denominated payments to different vendors

You can add the **same** denominated method on multiple rows, with
each row bound to a different vendor.  Example: a $5 Food Bucks for
the produce stall + a $5 Food Bucks for the cidery → two rows, each
with the same method but different vendor dropdowns.

Non-denominated methods (SNAP, Cash) stay one-row-per-method by
design — those distribute across vendors automatically.

## Single-vendor orders

When the customer's order has only one receipt, the vendor dropdown
disappears — there's no choice to make, and the binding is implicit.

## Why this matters for reports

The Vendor Reimbursement report attributes each denominated payment
to exactly the vendor it was given to — no fractional spreads, no
phantom Food Bucks on bakeries.  This was the architectural fix in
v1.9.9.
""",
        keywords=('denominated', 'food bucks', 'fmnp', 'vendor',
                  'dropdown', 'binding', 'multi-vendor', 'split'),
        related_articles=('split-payment', 'vendor-reimbursement',
                           'add-vendor'),
        screen='payment',
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

    # ── v1.9.9 articles ─────────────────────────────────────────────

    Article(
        id='device-tag',
        category_id='settings',
        title='Device Tag — what the "Device: A1B" chip in the header means',
        body="""
The header bar shows a chip labelled **Device: A1B** (or similar
3-character tag).  This is your laptop's **device tag** — a short
identifier appended to every customer label generated on this
machine.

## Why it exists

When five laptops are running at one market, every laptop is
independently generating sequential customer labels: C-001, C-002,
C-003 ...  Without a device tag, "look up customer C-005" is
ambiguous across the five devices, and the synced Google Sheets
report shows five different rows that all *display* as C-005
(separated by a hidden device_id column, but visually identical to
humans).

The device tag fixes that: every label this laptop generates ends
in `-{TAG}` so labels stay globally unique even in heavy-laptop
deployments.  Examples:

- Laptop A generates `C-005-A1B`
- Laptop B generates `C-005-LB1`
- Same sequence number, different labels, no ambiguity

## Where the tag comes from

By default, the tag is **auto-derived** from this machine's
unique hardware ID (Windows MachineGuid) — first 3 hex characters
of a SHA1 hash, uppercased.  It's stable per-device, so the same
laptop always gets the same tag without any setup.

You can override it with a friendly name in **Settings →
Preferences → Device Identity → Device Tag**.  Useful when you
want labels that match the physical sticker on each laptop:

- `LB1` for "Laptop 1"
- `MGR` for the manager's machine
- `OFC` for the office laptop

The override input accepts 1-4 alphanumeric characters; punctuation
and spaces are rejected.  Leaving it blank reverts to the
auto-derived tag.

## Where the tag appears

- The header chip — visible from every screen
- Every new customer label after the override is set
  (existing labels keep whatever tag they were created with)
- The "Customer ID" column in the Detailed Ledger and Geolocation
  Sheets reports

## When to override

Set a friendly tag if:

- You want labels coordinators can read at a glance ("L1 took 12
  customers today")
- You print physical labels and want the printed tag to match the
  laptop's sticker
- You're standardizing a multi-laptop deployment and want the
  tags consistent across markets

Leave the auto tag if:

- You only run one laptop per market (the tag still appears, just
  cosmetically — it never matters)
- You don't care which laptop captured which customer

## Things to know

- The tag is **per-device**, not per-market.  Two markets running
  on the same laptop share the same tag (which is fine — customer
  labels are scoped per market day, so cross-market collisions
  don't happen).
- Existing customer labels in your database from before v1.9.9 keep
  their old `C-NNN` format.  Only newly-created labels carry the
  tag.
- Changing the override does NOT rewrite existing labels.  It only
  affects labels generated from that point forward.
""",
        keywords=('device', 'tag', 'laptop', 'multi-device', 'collision',
                  'customer label', 'identifier', 'hash', 'override',
                  'multi-laptop'),
        related_articles=('returning-customer', 'sync-overview'),
        screen='settings',
    ),

    Article(
        id='unallocated-funds',
        category_id='corrections',
        title='Unallocated Funds — when an adjustment can\'t be collected',
        body="""
**Unallocated Funds** is a special payment category that records
money FAM absorbed because an adjustment increased what the
customer owed but the customer was no longer there to pay.

## When you'll see it

You'll be prompted on the **Adjustments page** whenever an
adjustment would require the customer to physically pay more than
they originally did.  Three scenarios trigger the prompt:

1. **Receipt total raised, breakdown not adjusted** — vendor
   reconciliation showed a higher total than originally entered;
   the customer would need to pay the gap.
2. **Customer payment increased in the breakdown** — you raised a
   payment row's amount while the receipt total stayed the same
   (e.g. correcting an under-recorded count of physical Food
   Bucks).
3. **Denomination overage** — physical instruments (Food Bucks,
   FMNP checks) overshoot the receipt by less than one full unit.
   Customer hands over a $5 check against $9 remaining → would
   pay $5 instead of $4 in real life.

## How the prompt works

A popup appears explaining the situation, with two options:

- **Yes — customer paid the additional amount** → save proceeds
  as you entered it.  Reports show the customer paying the higher
  total.
- **No — customer is gone, log as Unallocated Funds** → save
  records what the customer ACTUALLY paid (the original amount),
  and FAM absorbs the difference as Unallocated Funds.  The
  vendor still gets reimbursed in full so they're never short.

## What "absorbing" means

When you choose No:

- The customer's recorded payment stays at the original amount —
  the adjustment doesn't fabricate cash they never handed over.
- FAM contributes the difference (vendor still gets paid the full
  receipt).
- The Audit Log gets a dedicated `UNALLOCATED_FUNDS` entry
  describing exactly how much was absorbed.

## Where it appears in reports

- **FAM Match Report** — new "FAM Absorbed" column shows the
  total absorbed during the filter window, and the summary cards
  include a "FAM Absorbed" tile alongside "FAM Match".  These are
  intentionally separate: FAM Match is a multiplier on what the
  customer paid; FAM Absorbed is pure FAM funding that no customer
  contribution triggered.
- **Vendor Reimbursement** and **Detailed Ledger** — Unallocated
  Funds appears as a per-method column automatically (those tabs
  pivot dynamically on the method name).
- **Activity Log** — every Unallocated Funds injection shows up
  with the `UNALLOCATED_FUNDS` action and the dollar amount.

## Why this matters

Before this feature, an adjustment that increased what the
customer owed either:

- Saved a fictional "customer paid more" state (lying to the
  books), or
- Blocked the save with a "Payment Mismatch" error and forced
  the manager to manually rebuild the breakdown to match
  reality

Either way, FAM's books quietly absorbed the loss with **zero
accounting trail**.  Now every absorbed dollar shows up in the
audit log and the reports — coordinators can run a year-end
"how much did we absorb due to data-entry errors" tally directly.

## How to clean up later

If a customer DOES come back and pay an absorbed amount, treat it
as a fresh transaction (open a new market day, capture the
receipt, etc.) — don't try to retroactively edit the absorption
out.  The audit trail of "we absorbed it because they were gone,
later they paid" tells a cleaner story than "we never absorbed
anything in the first place".
""",
        keywords=('unallocated', 'absorbed', 'absorbing', 'customer gone',
                  'forfeit', 'fam absorbed', 'adjustment', 'lost funds',
                  'data entry error'),
        related_articles=('adjust-transaction', 'fam-match-report',
                          'denomination-forfeit'),
        screen='admin',
    ),

    Article(
        id='denomination-forfeit',
        category_id='during-market',
        title='Denomination forfeit — when a check overshoots the receipt',
        body="""
Some payment methods are **denominated** — they exist only in
fixed dollar increments.  FMNP checks come in $5 units; some Food
Bucks programs use $2 or $5 units.  You can't make change against
a denominated instrument: a customer hands over a whole $5 check
or doesn't, period.

## What happens when checks overshoot

If the receipt is $9 and the customer hands over two $5 FMNP
checks ($10 face value, 100% match), the math doesn't fit cleanly:

- Total method value: $10 customer + $10 FAM match = $20 of
  receipt coverage
- Receipt total: $9
- Overshoot: $11 — way more than the receipt warrants

The Payment screen and Adjustments page both handle this with a
**denomination forfeit**: FAM caps its match contribution at what
fits, and the customer "forfeits" the unmatched portion of FAM
match they would have gotten.

In the example above:

- Customer hands over $10 in checks ($10 customer_charged) — they
  paid the full face value
- FAM matches only $9 - $10 = nope, FAM contributes $0 because
  the customer's payment alone covers the receipt
- Vendor gets reimbursed $9 (the receipt) — which is less than
  the $10 face value handed over → customer effectively
  "donated" $1 toward the receipt

In a more realistic case (receipt $11, three $5 checks $15
face value, 100% match):

- Customer hands $15 + FAM $15 = $30 of value, but only $11 of
  receipt
- Forfeit: $19 of FAM match
- Saved record: customer $15, FAM match $0, vendor reimbursed $11
- The breakdown stays at three checks (the physical units the
  vendor actually has in hand) but the FAM match shrinks

## The popup at save time

When you confirm a payment that triggers a denomination forfeit,
a popup appears explaining the math and asking you to confirm:

- "This adjustment over-allocates the receipt by $X because the
  denominated payment cannot be broken into smaller increments."
- "The customer forfeits $X of FAM match (vendor still receives
  the full receipt amount)."
- If the customer's required payment ALSO went up vs. the original
  transaction (e.g. you recorded an extra check on adjustment),
  the popup additionally asks "Can the customer be charged the
  additional amount?" — Yes saves as-entered, No logs the gap as
  **Unallocated Funds**.

## Why "forfeit" not "refund"

The customer paid in real, indivisible physical instruments.
Refunding the difference would require giving the customer cash,
which most market booths can't do mid-shift.  The forfeit pattern
mirrors how the program runs in practice: customers occasionally
hand over a check whose value exceeds what they're buying, and
the program quietly absorbs the gap as goodwill.

## Tips for volunteers

- **You can't enter "half a check"** — the input stepper will
  only let you enter whole units.  If a customer's order is $9
  and they only have one $5 check, that's fine: capture the $5
  check + $4 from another payment method.
- **The cap is +1 unit, not unlimited** — the input lets you
  enter ONE unit beyond what would fit cleanly (the natural
  forfeit case), but rejects multiple-unit overshoots.  If the
  vendor reports more units than the receipt can absorb, that's
  a real over-allocation, not a denomination forfeit — re-check
  the count.
- **The forfeit popup lets you cancel** — clicking the "X" or
  the Cancel button returns to the breakdown so you can adjust.
""",
        keywords=('denomination', 'forfeit', 'fmnp', 'food bucks',
                  'overage', 'overshoot', 'check', 'token', 'physical',
                  'capped match', 'refund'),
        related_articles=('fmnp-via-payment', 'unallocated-funds',
                          'penny-reconciliation'),
        screen='payment',
    ),

    Article(
        id='adjustments-date-filter',
        category_id='corrections',
        title='Adjustments date filter — finding the transactions you worked on',
        body="""
The Adjustments page has a **Last Updated** date filter at the
top of the screen.  It's intentionally different from the Reports
screen's date filter — and that difference matters.

## What the filter targets

**Reports screen**: filters by **Market Date** (the business day a
transaction belongs to) — that's the right grouping for revenue
aggregation.

**Adjustments screen**: filters by **Last Updated** — the most
recent activity on the transaction.  That's the right grouping
for "what did I work on this week".

If you adjusted a 6-month-old transaction this morning, today's
filter window includes that transaction, even though its market
day was 6 months ago.  Pre-v1.9.9 the filter targeted market day,
which made the screen unusable for session review.

## What "Last Updated" means

It's the most recent of:

- The transaction's `created_at` (when it was first entered)
- Any audit_log entry referencing the transaction (adjustments,
  voids, payment changes — every audit action counts)

## Three dates per row

The Adjustments table now shows three dates per transaction so you
can correlate the filter to what you see:

| Column | What it shows | Example |
|---|---|---|
| **Market Date** | The business day this transaction's revenue belongs to | 2026-04-27 |
| **Created** | When the transaction was first entered into the app | 2026-04-29 11:42 |
| **Last Updated** | The most recent activity (filter target) | 2026-04-29 15:14 |

A transaction created on Monday and adjusted on Wednesday would
have Created = Monday, Last Updated = Wednesday.  Filtering for
"Wednesday" surfaces it; filtering for "Monday" does not.

## How to use it

- **"Show me what I worked on today"** — set both endpoints to
  today.  You'll see the day's new transactions and any adjustments
  to older ones.
- **"Show me the receipts I haven't reconciled this week"** — set
  the start of the week as the lower bound, leave the upper bound
  open.
- **"Show me a specific market day's transactions"** — instead of
  the date filter, use the Market dropdown.  The Market filter
  scopes to a single market_day_id; the date filter scopes to
  activity windows.

The filter triggers a live re-search every time the range changes
(no Search button needed for the date filter — the other filters
still respect the Search button).
""",
        keywords=('adjustments', 'date', 'filter', 'last updated',
                  'created', 'market date', 'session', 'reconciliation'),
        related_articles=('adjust-transaction', 'filter-reports',
                          'returning-customer'),
        screen='admin',
    ),

    Article(
        id='market-delete',
        category_id='settings',
        title='Deleting a market — when it\'s allowed',
        body="""
**Settings → Markets** offers two distinct ways to remove a
market: **Deactivate** and **Delete**.  They behave differently
on purpose.

## Deactivate

- Hides the market from new entry flows (it won't show up in the
  Market Day Setup dropdown anymore)
- **Keeps all historical data intact** — past market days,
  transactions, audit log entries all stay readable from Reports
- Reversible: click "Activate" to bring it back
- Use this when a market location closes for the season but you
  may want to reopen it next year

## Delete (red button)

- **Permanently removes** the market row
- **Only allowed when no `market_days` reference the market** —
  the handler runs a safety check before doing anything
- If the market has any history, you'll see a "Cannot Delete"
  warning explaining why and pointing you toward Deactivate
- Cascades cleanup of `market_vendors` and
  `market_payment_methods` junction rows (those are
  configuration, not data, so dropping them with the market is
  correct)
- Use this for accidentally-created entries that have never had
  a market day opened against them

## Why delete is gated

Markets don't carry a name snapshot on transactions or audit
entries — those rows reference `market_id` by foreign key.
Deleting a market with history would orphan its transactions
(they'd still exist in the database but couldn't be joined back
to a market name in reports).  The Deactivate path avoids this
trap entirely; Delete is only safe for never-used rows.

## A common scenario

Legacy entries from very early development sometimes survive in
real installs (e.g. a market named "M" with a $1.00 match limit
from the pre-v22 column default).  Such rows have no
transactional history and can be safely deleted via the new
button.

## What happens if you try

1. Click Delete on a market row
2. The handler queries `market_days` for any reference
3. If history exists → blocking warning, no action
4. If clean → confirmation dialog ("Delete 'X' permanently? This
   cannot be undone.")
5. Confirm → cascade-cleans junction rows, deletes the market,
   refreshes the table

The whole sequence is atomic: if any step fails, nothing changes.
""",
        keywords=('market', 'delete', 'remove', 'deactivate',
                  'cleanup', 'safety', 'orphan'),
        related_articles=('add-market', 'market-day-open'),
        screen='settings',
    ),

    Article(
        id='clear-error-log',
        category_id='reports',
        title='Clearing the Error Log',
        body="""
The **Error Log** tab on the Reports screen has a red **Clear
Errors** button.  It clears noise from the local log files AND
from the synced Google Sheets — but only the rows attributed to
THIS device.

## What gets cleared

- **`fam_manager.log`** in the data directory and any rotated
  backups (`fam_manager.log.1`, `.2`, ...) — truncated to empty
- **The "Error Log" tab on the configured Google Sheet** — only
  rows whose `device_id` column matches THIS laptop's device_id
  are removed; other devices' rows are preserved

## What is NOT cleared

- **The Audit Log / Activity Log** — those are regulatory history
  (every transaction adjustment, void, payment change) and stay
  intact.  Clear Errors only touches the technical error log.
- **Other devices' rows in the Sheets Error Log tab** — the
  cleanup is device-scoped on purpose.  In multi-laptop
  deployments, one coordinator's "clear my noise" should not wipe
  another laptop's diagnostic history.

## When to use it

- After investigating an issue you've already resolved
- When the Error Log has accumulated stale warnings from a fixed
  bug
- Before a fresh audit pass when you want to see only new errors

## Two-stage confirmation

The button asks twice before doing anything — once to confirm you
want to clear, and a second time to confirm you understand it
can't be undone.  After both confirmations, the local truncate
runs first, then the device-scoped Sheets cleanup, then the
in-app Error Log table refreshes.

## What happens if Sheets clearing fails

The local file truncation is independent of the Sheets cleanup.
If the Sheets call fails (no internet, permissions, etc.), the
local log is still cleared and the dialog reports the partial
success: *"Local file was still cleared.  The next sync will
overwrite the sheet with the (now empty) log."*

## Version-stamped error history

Every line in `fam_manager.log` carries a `[vX.Y.Z]` token
between the level and the logger name.  When you upgrade the app,
old log lines keep their original version stamp — the synced
Error Log report's "App Version" column shows exactly which
version produced each error.  This used to be silently rewritten
to the current version on every upgrade; v1.9.9 fixed that
provenance bug.
""",
        keywords=('error log', 'clear', 'clear errors', 'noise',
                  'diagnostic', 'fam_manager.log', 'version', 'sync',
                  'device-scoped'),
        related_articles=('export-reports', 'sync-overview'),
        screen='reports',
    ),

    # ══════════════════════════════════════════════════════════════════
    #   v1.9.10 ADDITIONS — Rewards, recovery runbooks, glossary, etc.
    # ══════════════════════════════════════════════════════════════════
    #
    # Added 2026-05-01 to close the gaps identified in the
    # documentation audit.  Most of these articles are written for a
    # market-day volunteer with no engineering background and no
    # immediate access to the project owner.  Each article opens with
    # a "What to do right now" summary so the actionable answer is
    # never more than a paragraph deep.

    Article(
        id='rewards-overview',
        category_id='settings',
        title='Customer Rewards — what they are',
        body="""\
**What to do right now:** Rewards are tokens, vouchers, or
extra dollars that you give the customer at the booth.  The app
records *what was earned* so the coordinator can reconcile;
giving the physical tokens is something you do in person.

## What rewards are

Rewards are an **add-on** that some markets run on top of the
FAM match — for example, "every $10 of SNAP earns $2 in produce
tokens."  Rules are configured by the coordinator in
**Settings → Rewards**.  When a payment confirms, the app:

- Computes which rules fired for this customer
- Shows a "GIVE TO CUSTOMER" zone on the confirmation dialog
- Records the rewards in the **Generated Rewards** report tab
- Includes a "Rewards Earned" block on the printed receipt

## Important — not financial

Rewards are **informational only**.  The app does not adjust
totals, vendor reimbursements, or FAM match math when rewards
fire.  The "Rewards Earned" line on a receipt is a *record* of
what you handed the customer; it is not subtracted from
anywhere.

## What you do at the booth

1. Confirm the payment as normal
2. Look at the GIVE TO CUSTOMER zone on the confirmation dialog
3. Hand the customer the listed tokens / vouchers
4. Click OK / Done

That's it — the app already wrote the row.

## What the coordinator sees

The **Generated Rewards** report tab and Google Sheet tab show
one row per (order × rule that fired).  Coordinators reconcile
this against physical token inventory.
""",
        keywords=('reward', 'rewards', 'tokens', 'voucher', 'food bucks',
                  'incentive', 'give'),
        related_articles=('rewards-configure', 'rewards-given-then-voided',
                          'enter-receipt', 'split-payment'),
        screen='settings',
    ),

    Article(
        id='rewards-configure',
        category_id='settings',
        title='Configuring Rewards rules (coordinator)',
        body="""\
**For coordinators only.**  If you are a volunteer at the
market, you don't need to configure anything — the rules are
already set up before market day.

## Where

**Settings → Rewards** tab.

## What a rule looks like

Each rule has:

- **Name** — what it's called (shown on receipts)
- **Trigger payment method** — which method, when used,
  fires the rule (e.g. SNAP)
- **Threshold** — how many dollars in that method must be
  used to earn one reward
- **Reward payment method** — which method represents the
  reward (e.g. "Produce Tokens")
- **Reward amount** — how many dollars per threshold met
- **Active** — toggle on/off without deleting

## Example

> "For every $5 of SNAP, the customer earns $2 in Produce Tokens"
> Trigger = SNAP, Threshold = 5, Reward = Produce Tokens, Amount = 2

A customer paying $13 of SNAP triggers the rule **2 times**
($5 + $5 fits twice; the trailing $3 doesn't reach the next
$5).  The customer earns 2 × $2 = **$4 of Produce Tokens**.

## Editing rules during a market day

It's safe to edit a rule mid-market — past confirmed orders
keep the rewards they were already given (the app stores the
rule snapshot at confirmation time).

## Disabling all rewards

Uncheck the master "Rewards enabled" toggle at the top of the
tab.  Confirmation dialogs will no longer show a GIVE TO
CUSTOMER zone, and printed receipts will skip the Rewards
Earned block.
""",
        keywords=('reward', 'rules', 'configure', 'threshold', 'rewards setup',
                  'add reward'),
        related_articles=('rewards-overview', 'rewards-given-then-voided',
                          'add-payment-method'),
        screen='settings',
    ),

    Article(
        id='rewards-given-then-voided',
        category_id='corrections',
        title='I gave tokens but the order was voided',
        body="""\
**What to do right now:** Note the customer label and how many
tokens you handed over.  Tell the coordinator at end-of-day so
they can subtract from inventory manually.  Don't try to
recover the tokens from the customer.

## Why this is tricky

Rewards are **physical objects** — once you've handed paper
tokens or a voucher to the customer, you can't put them back in
the drawer.  When the order is later voided (e.g. customer
changed their mind, you keyed it wrong), the app:

- Keeps the **Generated Rewards row** on the original date
  (it's a historical record of what you handed out)
- Marks the parent order Voided
- Does **not** add a "negative reward" or undo

This is intentional — pretending the tokens went back hides a
real inventory shortage from the coordinator.

## End-of-day reconciliation

The coordinator's process:

1. Pull the Generated Rewards report for the day
2. Count physical tokens given out (by you and other volunteers)
3. The two numbers should match
4. Any voided orders that gave tokens are flagged as
   "rewards-out, no-revenue" and reconciled against inventory
   or a small loss

## What to write on a sticky note

Customer label, tokens handed out, the void reason.  Hand it
to the coordinator with the day's deposit — ten seconds of
attention now beats a half-hour audit later.
""",
        keywords=('reward', 'voided', 'tokens', 'inventory', 'mistake'),
        related_articles=('rewards-overview', 'void-customer-order',
                          'void-vs-adjust'),
    ),

    Article(
        id='instance-lock-already-running',
        category_id='maintenance',
        title='"Another instance is already running" — what to do',
        body="""\
**What to do right now:**

1. Look at the Windows taskbar — is FAM Manager already open?
   Click it; you don't need to start a second copy.
2. If you don't see the app anywhere, open **Task Manager**
   (Ctrl + Shift + Esc), find any line that says **"FAM
   Manager.exe"**, click it, click **End task**.
3. Try launching FAM Manager again.

That fixes it 19 times out of 20.

## Why this happens

To protect your data, FAM Manager refuses to run two copies
against the same data folder at the same time.  Two running
copies could overwrite each other's records on the shared
Google Sheet.

The app enforces this with a small lock file at
`%APPDATA%\\FAM Market Manager\\.fam_instance.lock`.  When the
app launches, it claims the lock; when it exits cleanly, it
releases the lock.

## When the message lies

Sometimes Windows doesn't fully clean up when the app crashes,
and the lock looks held even though no process is actually
running.  In that case:

1. Confirm via Task Manager that **no** `FAM Manager.exe`
   process is running (kill any you find)
2. Open File Explorer, paste this path:
   `%APPDATA%\\FAM Market Manager\\`
3. Find the file **`.fam_instance.lock`**
4. Delete it
5. Launch FAM Manager normally

The lock will be re-created automatically on next launch.

## Don't do this if

If you're sure another volunteer or coordinator is running the
app on this same laptop, stop here — deleting the lock file
while the other copy is open is exactly what it's there to
prevent.

## When to call the coordinator

If the message keeps coming back even after Task Manager shows
no `FAM Manager.exe` and you've deleted the lock file, send a
diagnostic via **Help → System Status → Copy Diagnostic Info**.
""",
        keywords=('already running', 'instance', 'lock', "won't open",
                  "won't start", 'second copy', 'duplicate'),
        related_articles=('where-data-lives', 'pending-update-marker'),
    ),

    Article(
        id='pending-update-marker',
        category_id='maintenance',
        title='"Update did not complete" dialog — what to do',
        body="""\
**What to do right now:**

1. Note what the dialog says (especially the version numbers).
2. Click **OK** to dismiss.  Your data is safe — nothing was
   damaged.
3. Try one more update from **Settings → Updates → Check for
   Updates**.  If the app downloads and installs successfully,
   you're done.
4. If the second attempt fails the same way, follow the
   **manual update** steps below.

## What this dialog means

After you click "Download & Install", the app:

1. Writes a tiny note in your data folder saying "expecting to
   come back as version X.Y.Z"
2. Quits
3. The installer script copies the new files in
4. Relaunches the app
5. The new app reads the note, compares against its actual
   version

If the running version doesn't match what was expected, the
"Update did not complete" dialog fires.  This protects you from
**silent updater failures** — situations where the installer
exited cleanly but actually didn't replace the files.

## Why it might fail

- **Antivirus / SmartScreen** locked the new exe partway through
- **Disk was full** or the install drive was unplugged
- **The release zip didn't unpack correctly** (very rare since
  v1.9.4)
- **A second copy was running** during update and held the
  files open

## Manual update (when in-app fails)

1. Quit FAM Manager (close the window)
2. Open the Releases page in your browser:
   https://github.com/seansaball/fam-market-manager/releases
3. Download the latest `FAM_Manager_vX.Y.Z.zip`
4. Right-click the zip → Extract All
5. The extracted folder contains `FAM Manager.exe` — copy
   everything from that folder over your existing
   `C:\\Program Files\\FAM Manager\\` (or wherever the app is
   installed; right-click the desktop shortcut → Open file
   location to find out)
6. When Windows asks "replace these files?" click **Yes**
7. Launch from your usual shortcut

Your data folder
(`%APPDATA%\\FAM Market Manager\\`) is **never touched** by an
update — neither the in-app updater nor a manual install will
delete or rewrite your transactions.

## Rolling back (if the new version is broken)

The app keeps a backup of the previous installation at:

`%APPDATA%\\FAM Market Manager\\_update_backup\\`

If the new version misbehaves and you need to revert:

1. Quit FAM Manager
2. Copy everything from `_update_backup` over the install
   directory (same as manual update, but from the backup
   folder)
3. Launch — you're now back on the previous version

The data folder is still untouched.
""",
        keywords=('update failed', 'pending update', 'did not complete',
                  'rollback', 'revert', 'manual update', 'silent failure'),
        related_articles=('check-for-updates', 'manual-install',
                          'where-data-lives'),
    ),

    Article(
        id='offline-saturday-runbook',
        category_id='during-market',
        title='Working a market with no internet — full runbook',
        body="""\
**What to do right now:** Keep working.  The app is fully
functional offline.  Sync will happen automatically when
internet returns.

## What works offline

Everything you do at the booth:

- Open / close market days
- Receipt Intake
- Payment Screen with FAM match calculation
- FMNP Entry (taking the photo too — Windows stores it
  locally; the upload to Drive is what's deferred)
- Adjustments
- Reports (every tab works on local data)
- Printing receipts (if your printer is connected via USB)

## What doesn't work offline

Only sync.  Specifically:

- "Sync to Cloud" button — disabled until internet is back
- Auto-sync timer — quietly skips and tries again later
- Auto-update check — quietly skips
- The shared Google Sheet won't update with your data until
  you sync

## How you know it's safe

The bottom-right indicator chip in the title bar:

- **Gray dot, "No network"** — Windows reports no internet.
  This is normal offline.  Local data is fine.
- **Yellow / red** — internet is back but a sync attempt
  failed.  Different problem; see *The sync indicator is red*
  troubleshooting flow.

## Belt-and-suspenders backup

Even before your laptop's internet returns, the app is writing
to **two** backups every time you confirm a transaction:

1. **Database snapshot** in
   `%APPDATA%\\FAM Market Manager\\backups\\`
2. **Plain-text ledger** in
   `%APPDATA%\\FAM Market Manager\\fam_ledger_backup.txt` —
   you can open it in Notepad to see the day's transactions
   in plain text (useful as a sanity check)

If your laptop dies completely mid-market, those two files
are how the next laptop reconstructs the day.

## End-of-market checklist (offline day)

1. Close the market day as normal
2. Take the laptop somewhere with Wi-Fi (your home router is
   ideal)
3. Wait a minute — auto-sync runs every 5 minutes once
   internet is back
4. Open Settings → Cloud Sync, click **Sync to Cloud** to
   force the sync immediately if you don't want to wait
5. Confirm the indicator chip turns green
6. Verify the day's data appears in the shared Google Sheet
   (filter by today's date)

## Common worry: "did I lose anything?"

No.  The app's local SQLite database is the source of truth.
Sync only **mirrors** that data to the Sheet.  As long as the
SQLite file is intact (which it is unless your laptop's hard
drive failed), you have everything.
""",
        keywords=('offline', 'no internet', 'wifi', 'no network', 'no signal',
                  'works offline', 'lost data', 'sync later', 'saturday'),
        related_articles=('no-network-data-safe', 'offline-operation',
                          'backups', 'data-not-on-sheet'),
    ),

    Article(
        id='data-not-on-sheet',
        category_id='sync',
        title='My transactions are not showing on the shared Sheet',
        body="""\
**What to do right now:** Check three things in order — most
of the time it's #1.

## 1. Did sync actually run?

Look at the chip in the title bar.

- **Green** = last sync succeeded.  Your rows ARE on the
  sheet — see step 2 if you can't find them.
- **Red / yellow / gray** = sync hasn't reached the sheet
  yet.  See *The sync indicator is red* or *Sync indicator
  says "No network"* troubleshooting flows.

If the chip is green but you don't see the data on the sheet:
**force a re-sync.**  Settings → Cloud Sync → Sync to Cloud.
Wait for the green confirmation, refresh your browser.

## 2. Are you looking at the right tab and the right day?

The shared Google Sheet has multiple tabs.  Each report goes
to its own tab:

| Looking for...                | Tab name              |
|-------------------------------|-----------------------|
| Vendor totals                 | Vendor Reimbursement  |
| FAM match by payment method   | FAM Match Report      |
| Every transaction             | Detailed Ledger       |
| Confirms / voids history      | Transaction Log       |
| FMNP check entries            | FMNP Entries          |
| Rewards given                 | Generated Rewards     |
| End-of-day market summary     | Market Day Summary    |

Open the right tab and filter by today's date.  Each row also
has **`market_code`** and **`device_id`** columns at the far
left — those tell you which market and which laptop produced
the row.

## 3. Are multiple laptops syncing to the same Sheet?

If your market runs more than one laptop, each laptop
contributes its own rows.  Filter the Sheet by your laptop's
**`device_id`** to see only your rows.  Open Help → System
Status to find this laptop's device_id.

## The "missing rows came back" mystery

Sometimes a row appears, gets edited locally, gets synced
again, and a coordinator looking at the sheet between syncs
sees an inconsistent state.  This is normal — give it a
minute.  The sync engine writes the row from your device's
"latest known" state every time it runs.

## The "I see someone else's rows" non-mystery

The shared Sheet is *shared*.  You see every market and every
laptop that uses the same Spreadsheet ID.  Filter by
`market_code` (the 4-letter code in the title bar of FAM
Manager) to see only your market's rows.

## When to escalate

If after force-syncing, checking the right tab, and filtering
by your `device_id` you still don't see today's rows:

1. Help → System Status → Copy Diagnostic Info
2. Paste it into an email along with the day's date and the
   tab you're looking at
3. Send to your coordinator

Your local data is safe regardless of what's on the Sheet —
re-syncing later cannot lose it.
""",
        keywords=('not showing', 'missing', 'sheet empty', 'rows missing',
                  "can't find", 'not on sheet', 'where is my data', 'verify sync'),
        related_articles=('sync-overview', 'sync-indicator', 'connect-sheets',
                          'sync-failed'),
    ),

    Article(
        id='restore-from-backup',
        category_id='maintenance',
        title='Restoring data from a backup — step by step',
        body="""\
**Read first:** This is a recovery procedure, not a routine
operation.  Only follow these steps if your data is actually
gone or visibly wrong.  If you're unsure, take a copy of the
data folder before doing anything else.

## When to use this

- The app launches but Reports show zero transactions for a
  market day you know happened
- The app crashes immediately on launch with a database error
  (see step 1 below to confirm before restoring)
- A coordinator has explicitly told you to restore

## When NOT to use this

- The shared Google Sheet looks wrong but the app's local
  Reports are fine.  This is a sync problem, not a database
  problem — see *My transactions are not showing on the
  shared Sheet*.
- A specific transaction is wrong.  Use **Adjustments**
  instead — see *Editing a confirmed transaction*.
- Photos are missing from Drive.  See *Photo says "Pending"
  forever*.

## Step 0 — Make a safety copy

Before restoring anything:

1. Quit FAM Manager (close the window completely)
2. Open File Explorer, paste this path into the address bar:
   `%APPDATA%\\FAM Market Manager\\`
3. **Copy the entire folder** to your Desktop (right-click →
   Copy → paste on Desktop).  Name the copy
   `FAM Backup BEFORE RESTORE 2026-MM-DD`.

If anything goes wrong, you can put this safety copy back.

## Step 1 — Confirm a backup exists

Inside `%APPDATA%\\FAM Market Manager\\backups\\` you should
see a list of files named like:

- `fam_2026-05-01_09-15-00.db` (auto-backup at 9:15 AM)
- `fam_2026-05-01_09-20-00.db` (5 minutes later)
- `fam_2026-05-01_market_open.db` (one-shot at market open)

Backups are taken every 5 minutes during a market day, plus
one extra at market_open and market_close.  Pick the most
recent backup that pre-dates the problem.

## Step 2 — Restore

1. With FAM Manager closed, navigate to
   `%APPDATA%\\FAM Market Manager\\`
2. **Rename** the existing `fam_data.db` to
   `fam_data_BROKEN.db` (don't delete it yet — you might need
   to look at it later)
3. **Copy** your chosen backup file from
   `backups\\fam_2026-XX-XX_XX-XX-XX.db` up one level into
   `%APPDATA%\\FAM Market Manager\\`
4. **Rename** the copy to exactly `fam_data.db`
5. Launch FAM Manager
6. Open Reports — verify the data looks correct as of the
   backup time you picked

## Step 3 — Reconcile

If the backup is recent (within 5 minutes of the problem),
you may not need to do anything else.  If it's older, you'll
need to re-enter transactions that happened between the
backup time and the failure.

The plain-text **ledger backup** at
`%APPDATA%\\FAM Market Manager\\fam_ledger_backup.txt`
preserves a record of every confirmed transaction in plain
English.  Open it in Notepad — anything past the backup's
timestamp is what you'll need to re-enter.

## Step 4 — Push to the Sheet

After you've restored and re-entered as needed, click
Settings → Cloud Sync → Sync to Cloud to bring the shared
sheet into agreement with your local state.

## When this doesn't help

If even the backups are corrupt, or `fam_data.db` is fine but
the app refuses to launch:

1. Help → System Status → Copy Diagnostic Info (you can do
   this from a fresh laptop pointing at the broken data
   folder)
2. Email it with the timestamp of when things went wrong
3. The coordinator can recover from the **Detailed Ledger
   tab on the shared Google Sheet** if it was syncing — the
   sheet is a complete external record.
""",
        keywords=('restore', 'backup', 'recover', 'data lost', 'corrupted',
                  'database', 'rollback', 'broken', 'crashed'),
        related_articles=('backups', 'where-data-lives',
                          'pending-update-marker'),
    ),

    Article(
        id='glossary',
        category_id='getting-started',
        title='Glossary — what every term means',
        body="""\
Plain-English definitions of every term that shows up in the
app, the printed receipt, the Google Sheet, and these help
articles.

## App and people

**FAM** — Food Assistance Match.  The subsidy program your
market participates in.  When the app says "FAM match," it
means the dollars FAM contributes on top of what the customer
pays.

**FMNP** — Farmers' Market Nutrition Program.  A state-funded
voucher / check program separate from FAM.  See *FMNP
overview*.

**Vendor** — a farmer / seller at the market.

**Customer** — the shopper.  The app uses a short label
(e.g. C-005) to track them across multiple receipts on the
same day.

**Coordinator** — the person who runs the market or the FAM
program.  Configures Settings, troubleshoots issues, and
reconciles end-of-day reports.

**Volunteer** — that's you, at the booth.

## Identifiers

**market_code** — the 4-letter code for your market location
(e.g. `BPFM` for Bethel Park, `BVFM` for Bellevue).  Set in
Settings → Markets.  Shows in the title bar in brackets.

**device_id** — a short tag identifying *this laptop*.  Used
on the shared Google Sheet to tell which laptop produced
which row.  Defaults to a 3-character auto-derived tag; you
can customize in Settings → Preferences → Device Identity.

**fam_transaction_id** — the unique ID for a transaction,
formatted like `FAM-BPFM-20260501-0001`.  Means: FAM | the
market_code | the date | a 4-digit number for the day.
Adjustments search this field.

## Concepts

**Composite key** — when the shared sheet matches a row by
multiple columns at once (e.g. market_code + device_id +
date + customer label), preventing two laptops from
accidentally overwriting each other's data.

**Upsert** — short for "update or insert."  When syncing,
the app updates a row if one with the same composite key
already exists, or inserts a new row if not.

**Audit log** — an append-only history of every change in
the database (confirms, voids, adjustments, edits).
"Append-only" means rows are added but never modified or
deleted, so it's a permanent record.

**Service account** — a Google identity used by the app to
authenticate to Sheets and Drive.  Created in the Google
Cloud console; the credentials JSON file is the secret that
proves the identity.  Coordinators handle this; volunteers
just receive the file once and load it.

**Drive folder ID** — the long string in a Google Drive
folder URL.  The app uploads photos to this folder.

**Spreadsheet ID** — the long string in a Google Sheets URL.
Identifies the shared workbook the app syncs to.

## Money math

**Match cap** — the maximum FAM dollars a single market day
can spend.  Configured per market in Settings.  Once hit,
new orders show "Cap reached" and FAM contributes 0.

**Daily match cap** — same as match cap.

**Match percent** — for each payment method, the percentage
FAM matches.  E.g. SNAP at 100% means $1 SNAP earns $1 FAM
match.  FMNP at 50% means $5 FMNP earns $2.50 FAM.

**Penny reconciliation** — rounding cents so that the totals
on a multi-method payment add up exactly to the receipt
total.  The app handles this automatically.

**Drift / drift cent** — the 1¢ rounding leftover that the
app distributes between methods to keep totals exact.

**Denominated payment** — a payment where the amount can
only be a multiple of a fixed denomination (e.g. Food Bucks
in $5 increments).  The app prevents you from entering a
non-multiple.

**Forfeit** — when a denominated payment overshoots the
receipt (customer hands over $15 of FMNP for an $11
receipt).  The vendor gets the full $11; the customer
"forfeits" the unmatched $4 of physical paper.  See
*Denomination forfeit*.

**Unallocated funds** — money that was on a confirmed
transaction but isn't covered by any line after an
adjustment.  Shows in the audit log as a UNALLOCATED_FUNDS
action.

## Sync

**Composite-key upsert** — see Composite key + Upsert above.
What the sync engine does to merge multi-laptop data without
collisions.

**Sync indicator chip** — the colored dot in the title bar
showing the latest sync state (green/yellow/red/gray).

**60-write/min quota** — Google's rate limit on Sheets
writes.  The app paces itself to stay under this.

**5-minute auto-sync** — the timer that triggers sync
automatically while a market day is open.

## Files and folders

**Data folder** — `%APPDATA%\\FAM Market Manager\\` —
where everything lives.

**fam_data.db** — the main SQLite database file.  Source of
truth.

**WAL** — Write-Ahead Log, a file SQLite uses while
committing.  You'll see `fam_data.db-wal` and
`fam_data.db-shm` next to the main file.  Don't move or
delete them while the app is running.

**Backup** — a `.db` file copied to `backups/` at fixed
intervals during a market day.

**Ledger backup** — `fam_ledger_backup.txt`, a plain-text
human-readable copy of every confirmed transaction.

**Pending-update marker** — `_pending_update.json`.  A short
note left behind by the updater so the new version can
verify it actually installed.

**Instance lock** — `.fam_instance.lock`.  Prevents two
copies of the app from running against the same data folder.

## Status words

**Confirmed** — the operator clicked Confirm; FAM has
committed match dollars.

**Voided** — the transaction was cancelled.  Match dollars
are released; the customer didn't pay.

**Adjusted** — a confirmed transaction was edited later.
The audit log records what changed.

**Soft delete** — the row stays in the database but is
hidden from normal views.  Used when something is "deleted"
but still needs to be referenced for history.
""",
        keywords=('glossary', 'definitions', 'terms', 'meaning', 'jargon',
                  'what does', 'vocabulary'),
        related_articles=('what-is-fam-manager', 'sync-overview',
                          'where-data-lives', 'fmnp-overview',
                          'penny-reconciliation'),
    ),

    Article(
        id='multi-laptop-deployment',
        category_id='settings',
        title='Running multiple laptops at the same market',
        body="""\
**What to do right now:** As long as each laptop has been
configured by the coordinator with the right `market_code`
and a unique `device_id`, you can use them at the same time
without coordination.  Just keep working — the app handles
the rest.

## How it works

When two or more laptops share the same `market_code` (same
market) and each has a different `device_id` (different
laptop), the shared Google Sheet treats them as **independent
contributors**.  Each row carries both fields, so even when
two volunteers confirm orders at the same instant on
different laptops, their rows don't overwrite each other.

## Customer labels can repeat

Each laptop assigns its own customer labels (C-001, C-002,
…).  Two laptops will both have a customer "C-005" — these
are different customers.  This is fine: every transaction
also carries the device_id, so reports keep them separate.

The coordinator's view of the sheet shows all of them
together.  Filter by `device_id` to see one laptop's
customers in isolation.

## When you switch laptops mid-day

If you start the day on laptop A and switch to laptop B for
the second half:

- Laptop B can keep working — same market, different
  device_id
- Laptop B does NOT see laptop A's customers in its local
  Reports (they live in laptop A's database)
- The shared Google Sheet shows everyone's data merged
- For end-of-day, run reports on each laptop separately, or
  pull from the sheet

## What NOT to do

**Don't run two copies on the same laptop.**  The app blocks
this with the instance lock.  See *"Another instance is
already running"*.

**Don't copy the database file between laptops.**  Doing so
clones the device_id and breaks the sheet's ability to tell
the laptops apart.  Use the export-settings / import-settings
mechanism for sharing setup, not the database.

**Don't reconfigure device_id mid-day.**  If you change the
device_id while the day is in progress, the sheet will see
two "different" devices contributing the same data — your
rows will appear to duplicate.

## Coordinator setup checklist

For each laptop before its first market day:

1. Install the app
2. Load defaults or import the standard `.fam` settings file
3. Set the **market_code** in Settings → Markets to match the
   market this laptop covers
4. Set a unique **device tag** in Settings → Preferences →
   Device Identity (e.g. `LB1`, `LB2`)
5. Load Cloud Sync credentials and Spreadsheet ID
6. Test sync — confirm a row appears on the sheet with the
   right device_id

## End-of-day for multi-laptop markets

The shared Google Sheet is the merged view.  Pull reports
from there, not from individual laptops, when you need
totals across the whole market.
""",
        keywords=('multi-laptop', 'two laptops', 'multiple devices',
                  'shared market', 'two volunteers', 'second laptop'),
        related_articles=('device-tag', 'sync-overview',
                          'instance-lock-already-running',
                          'data-not-on-sheet'),
    ),

    Article(
        id='diagnostic-info-no-internet',
        category_id='maintenance',
        title='Sending diagnostic info without internet',
        body="""\
**What to do right now:** Open Help → System Status → click
**Copy Diagnostic Info** → paste into a Notepad file → save
the Notepad file with the day's date in the name → carry it
home on a USB stick or just type it up later.  No internet
needed.

## The clipboard approach (when you have at least a phone)

1. Help → System Status → Copy Diagnostic Info
2. Open Notes / Mail on your phone
3. Type your message
4. Long-press → Paste
5. Send when you have signal

## The "no signal at all" approach

1. Help → System Status → Copy Diagnostic Info
2. Open **Notepad** on the laptop (Start menu, type "notepad")
3. Paste (Ctrl + V)
4. File → Save As → save to Desktop with a name like
   `FAM diagnostic 2026-05-01.txt`
5. Either:
   - Plug a USB stick in, copy the file to it, take it home
   - Email it later when you have Wi-Fi
   - Photograph the screen with your phone and send the photo

## What to include alongside the diagnostic

The diagnostic block tells the coordinator what the system
*looks* like.  Add what *happened* in your own words:

- What you were trying to do when it went wrong
- What you saw on screen (any error message, take a phone
  photo)
- The customer label / vendor / time if relevant
- Whether you had a workaround (kept going, or had to stop)

## What's in a diagnostic info block

Useful fields a coordinator will look for:

- App version (e.g. `1.9.10`)
- Last sync timestamp (was it minutes ago or days?)
- Last sync error (if any)
- Open market day (was one open when the issue happened?)
- Counts of confirmed / voided transactions
- Disk space used by the database / photos / backups

The block does not contain any customer names, payment-card
numbers, or personally identifying information.
""",
        keywords=('diagnostic', 'support', 'send log', 'no signal', 'usb',
                  'system status', 'help', 'no internet'),
        related_articles=('where-data-lives', 'offline-saturday-runbook',
                          'rotate-credentials'),
    ),

    Article(
        id='rotate-credentials',
        category_id='sync',
        title='Replacing the Google credentials file (coordinator)',
        body="""\
**For coordinators only.**  Volunteers don't need to do this.

## When you need to do it

- The service account was rotated (security policy)
- A new market joined and got its own service account
- The old file expired or was revoked
- A coordinator handover and the new person creates a new
  service account

## What you need

A new credentials JSON file from Google Cloud Console.
Generate it from the same project that owns the shared
Google Sheet, or from a new project — either works as long
as the new service account email is shared on the sheet
**with Editor access**.

## Steps (per laptop)

1. Settings → Cloud Sync → click **Load Credentials**
2. Pick the new `.json` file
3. Click **Save Sync Settings**
4. Click **Sync to Cloud** to verify

If sync succeeds (green chip, no error in tooltip), you're
done.  If sync fails with a permission error, the new
service account email isn't shared on the Sheet — fix that
in Google Sheets first (Share → paste the service account
email → set to Editor → no notification needed).

## Where the file ends up

The credentials are copied into
`%APPDATA%\\FAM Market Manager\\google_credentials.json`
on each laptop.  Do not edit this file by hand.

## Rolling back

If the new credentials don't work, just Load Credentials
again with the old file (assuming you kept a copy).  Nothing
in the data folder cares about credential identity — only
the active credentials matter.

## Multi-laptop deployment

You'll need to repeat Load Credentials on every laptop the
new credentials should work on.  There's no way to push the
new file to all laptops remotely — it's a per-machine action.
""",
        keywords=('credentials', 'rotate', 'service account', 'new key',
                  'expired', 'auth error', 'revoked'),
        related_articles=('connect-sheets', 'connect-drive',
                          'sync-overview', 'data-not-on-sheet'),
    ),

    Article(
        id='end-of-day-handoff',
        category_id='reports',
        title='End-of-day coordinator hand-off checklist',
        body="""\
**Use this at market close.**  A 5-minute checklist to
guarantee the coordinator has everything they need.

## At the booth

1. **Close the market day** (Market screen → Close button)
2. Take a photo of the deposit slip / cash count if your
   market does cash reconciliation
3. **Sync to Cloud** (Settings → Cloud Sync → button) — wait
   for green
4. Verify the indicator chip is green

## In the app

Open Reports.  Take a screenshot or print each of these:

- **Vendor Reimbursement** — what each vendor is owed
- **FAM Match Report** — total match dollars by payment
  method
- **FMNP Entries** — FMNP checks taken (if your market does
  FMNP)
- **Detailed Ledger** — every transaction
- **Generated Rewards** — any tokens given out (if your
  market does rewards)

If your laptop has a printer, the **Print Reports** menu
prints all of these in one go.

## Things to send the coordinator

In one email:

- The day's date and market location
- The number of confirmed transactions (from System Status
  → "Confirmed transactions")
- Any voided orders that gave rewards (handwritten note
  during the day — see *I gave tokens but the order was
  voided*)
- Any in-app errors you saw (paste from Help → System
  Status if anything was off)
- The cash deposit photo if relevant

If your market uses the shared Google Sheet, the coordinator
already has all the row-level data — you don't need to send
the reports themselves; the email is just a "here's what
happened today" summary.

## Last steps

1. Quit FAM Manager normally (close the window)
2. Confirm the laptop's lock file is gone
   (`%APPDATA%\\FAM Market Manager\\.fam_instance.lock`
   should not exist after a clean shutdown — see
   *"Another instance is already running"* if you're
   troubleshooting)
3. Power off the laptop

## Multi-laptop markets

Each laptop runs its own end-of-day.  The shared Sheet
merges everything; the coordinator pulls totals from there.

## When you can't reach the coordinator

If you're locked out of email or the coordinator is
unreachable, **Help → System Status → Copy Diagnostic Info**
captures everything they'd need to triage remotely.  Save
that to a USB stick or your phone — see *Sending diagnostic
info without internet*.
""",
        keywords=('end of day', 'handoff', 'close market', 'reports',
                  'coordinator', 'reconcile', 'finish'),
        related_articles=('market-day-close', 'sync-overview',
                          'rewards-given-then-voided', 'export-reports',
                          'diagnostic-info-no-internet'),
        screen='reports',
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
            "8. If the cap math is correct but the customer still wants to spend more in mixed payment methods (denominated tokens + SNAP) and the screen is hard-blocking the confirm — split the receipts into separate customer orders, one payment method per order.  See 'Hard block on the Payment screen — math doesn't reconcile' for the step-by-step.",
        ),
        keywords=('cap', 'limit', 'wrong', 'exceeded', 'warning'),
        related_articles=('match-cap', 'returning-customer', 'penny-reconciliation',
                          'split-orders-when-stuck'),
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

    # ── v1.9.9 troubleshooting flows ────────────────────────────────

    TroubleshootingFlow(
        id='ts-customer-id-collisions',
        title='Multiple laptops have the same customer ID',
        symptom='Coordinators report that "C-005" exists on more than one laptop, or the synced sheet shows duplicate-looking customer IDs',
        steps=(
            "1. Confirm you're on v1.9.9 or later — the device tag fix shipped in v1.9.9. Help → System Status shows the app version.",
            "2. Check the header chip on each laptop — every device shows a 'Device: XXX' chip. The 3-char tag should be different on every machine.",
            "3. If two laptops show the SAME tag, one of them has a manual override set to a colliding value — Settings → Preferences → Device Identity → Device Tag.  Clear the override on one and set a unique one (e.g. 'LB1' on one, 'LB2' on the other).",
            "4. Customer labels generated AFTER the fix carry the tag (e.g. 'C-005-A1B').  Existing labels from before v1.9.9 keep their old format ('C-005') — those collisions can't be retroactively fixed but no new ones will appear.",
            "5. To rename pre-v1.9.9 labels for clarity, you'd need a database edit (out of scope for in-app workflows).  Most installs just live with the historical legacy labels and let the new ones disambiguate going forward.",
        ),
        keywords=('customer id', 'collision', 'duplicate', 'multi-laptop',
                  'device tag', 'C-005', 'multiple', 'same'),
        related_articles=('device-tag', 'returning-customer'),
    ),

    TroubleshootingFlow(
        id='ts-cant-delete-market',
        title='I can\'t delete a market — only Deactivate is offered',
        symptom='A market in Settings → Markets only has a Deactivate button visible, no Delete',
        steps=(
            "1. Verify you're on v1.9.9 or later — the Delete button shipped in v1.9.9. Help → System Status shows the app version.",
            "2. The Delete button is a separate red button next to Deactivate. If it's not appearing, the actions column may be too narrow — try resizing the Settings window wider or scrolling the row horizontally.",
            "3. Click Delete. If you get a 'Cannot Delete' warning, the market has historical market_days (and therefore transactions) referenced — those would orphan if the market were dropped. Use Deactivate instead.",
            "4. If the warning surprises you (you don't recall ever opening a market day for this entry), click the row's Edit button to confirm you're looking at the right market, then check Reports → Detailed Ledger filtered by that market name to see what history exists.",
            "5. If the market truly has no history (e.g. an accidental entry never used), Delete will offer a confirmation dialog and remove it cleanly along with its junction-table configuration.",
        ),
        keywords=('market', 'delete', 'cannot delete', 'remove',
                  'orphan', 'M', 'legacy'),
        related_articles=('market-delete', 'add-market'),
    ),

    TroubleshootingFlow(
        id='ts-adjustment-blocked-by-mismatch',
        title='Adjustment save is blocked with "Payment Row Mismatch"',
        symptom='You hit OK on an Adjustment dialog and get a "Payment Row Mismatch" warning instead of save',
        steps=(
            "1. Read the dollar amounts in the warning — it shows the row's typed value AND the engine's computed charge (e.g. shows $50 but engine computed $60).",
            "2. The mismatch usually means the daily match cap inflated the customer's required payment past what's typed.  Click ⚡ Auto-Distribute on the Adjustment dialog — that runs the engine and writes the correct values back into the rows.",
            "3. Alternatively, manually adjust the row to match the engine's expected charge (the value shown in the customer-impact panel below the rows).",
            "4. Save again — the guard should now pass.",
            "5. This guard exists to prevent silent under-charging.  It mirrors the same protection on the Payment screen and is intentional: if the typed value disagrees with the engine, the saved record would be wrong.",
            "6. If Auto-Distribute does not fix it after a click or two — the dialog explicitly mentions the daily cap, or the gap won't go away — the cleanest resolution is to **Cancel** the Adjustment, **Void** the original transaction (Adjustments → Void), and re-enter the customer's purchases as **separate orders one method at a time** from Receipt Intake.  See the troubleshooting flow 'Hard block on the Payment screen — math doesn't reconcile' for the step-by-step.",
        ),
        keywords=('payment row mismatch', 'adjustment', 'cap', 'auto-distribute',
                  'charge integrity', 'guard'),
        related_articles=('adjust-transaction', 'penny-reconciliation',
                          'split-orders-when-stuck'),
    ),

    # v2.0.7: hard-block on Payment screen → split-into-separate-orders
    TroubleshootingFlow(
        id='ts-payment-screen-hard-block',
        title='Hard block on the Payment screen — math doesn\'t reconcile',
        symptom='Payment screen refuses to confirm: "Payment row mismatch", per-vendor over- or under-allocation, "non-denom method exceeds capacity", or the engine just won\'t balance no matter what you change',
        steps=(
            "1. Read the dialog carefully.  If it explicitly recommends 'splitting the customer's receipts into two separate customer orders', go straight to step 5.  That message is fired by the cap-bound detector and the recommendation is the supported resolution.",
            "2. Click ⚡ Auto-Distribute once and try Confirm again.  If that clears the dialog, you're done.",
            "3. If Auto-Distribute didn't fix it: read the row values vs. the Collect-from-Customer panel (below the rows) — they should match exactly.  If a row shows a different number than the panel, type the panel's number into the row and Confirm again.",
            "4. If the dialog mentions an ineligible vendor by name, see the troubleshooting flow 'Per-vendor eligibility error on Payment'.  Splitting will not help with that case — the issue is which vendor accepts which method.",
            "5. **Best resolution when the math just won't reconcile:** break the receipts into separate customer orders, one payment method per order.  This is the cleanest, safest path and works for every cap-bound, denomination-aware, multi-method scenario.  Sequence:",
            "   a. Click **Cancel** on the Payment dialog.  Do NOT click Confirm.",
            "   b. Go back to **Receipt Intake** and click **Discard** on the in-progress order (or **Pending Orders → Discard** if you saved it as a draft).  Your typed receipts are not lost — you'll re-add them in step c using the same numbers.",
            "   c. Create a new order for the **same customer label** (use the returning-customer dropdown or type the label) and add ONLY the receipts that one payment method will cover (e.g. just the Food RX portion).",
            "   d. Go to **Payment**, enter only that one method, ⚡ Auto-Distribute if needed, Confirm.",
            "   e. Repeat from c with the remaining receipts and the next payment method (e.g. SNAP for the rest).",
            "6. Because the customer label is the same on every order, the daily match cap accounting carries through automatically — the second order sees the first's match already used and adjusts.  Reports still group by customer label, so the customer's day rolls up to one summary row per category.  Nothing is lost.",
            "7. If even split orders don't reconcile (very rare): Help → System Status → Copy Diagnostic Info, then send to your coordinator with a screenshot of the dialog.  In the meantime, write the customer's purchases on paper and confirm what you can; the coordinator can reconcile the rest later via Adjustments.",
        ),
        keywords=('hard block', 'mismatch', 'cannot confirm', 'cap-bound',
                  'split', 'separate orders', 'won\'t balance',
                  'over-allocation', 'under-allocation', 'stuck',
                  'reconcile', 'workaround', 'multiple orders'),
        related_articles=('split-orders-when-stuck', 'match-cap',
                          'cap-warning', 'returning-customer',
                          'auto-distribute-button', 'split-payment'),
    ),

    TroubleshootingFlow(
        id='ts-stale-market-day-popup',
        title='App says "Stale market day was auto-closed" at startup',
        symptom='Opening the app shows a notification that an older market day was closed automatically',
        steps=(
            "1. This is normal v1.9.9 behaviour, not a bug.  A market day was left Open with a date earlier than today, so the app closed it automatically to prevent new transactions from being mis-attributed to a past day.",
            "2. The closed day's transactions are intact — no data was lost.  Check Reports → Detailed Ledger for that date to confirm.",
            "3. To start work today, open a new market day on the Market Day Setup screen.  Pick today's market location and volunteer name, then Open Market Day.",
            "4. The dialog typically shows the market name and date that was auto-closed so you know exactly what happened.",
            "5. Going forward: close the day at the end of every market.  Settings → Reset doesn't auto-close days for you; the auto-close only kicks in at startup when the existing Open day's date is in the past.",
        ),
        keywords=('stale', 'auto-close', 'market day', 'past date',
                  'startup', 'forced close'),
        related_articles=('market-day-open', 'market-day-close',
                          'market-day-reopen'),
    ),

    # ── v1.9.10 additions ──────────────────────────────────────────

    TroubleshootingFlow(
        id='ts-update-failed',
        title='"Update did not complete" dialog at startup',
        symptom='After clicking Download & Install, the app reopens but shows a yellow warning that the update did not finish',
        steps=(
            "1. Click OK to dismiss.  Your data is safe — this dialog is the safety net, not a sign of damage.",
            "2. Settings → Updates → click 'Check for Updates' once more.  Most of the time the second attempt succeeds.",
            "3. If it fails the same way, look at the version numbers in the dialog.  If the 'expected' and 'actual' differ by a major version (e.g. 1.9.10 expected, 1.9.5 actual), the installer probably hit antivirus or SmartScreen.",
            "4. Open Help → Browse → 'Update did not complete' for the manual install steps.",
            "5. To rollback to the previous version: quit the app, copy everything from %APPDATA%\\FAM Market Manager\\_update_backup\\ over your install directory.",
            "6. If you remain stuck, Help → System Status → Copy Diagnostic Info and email it to your coordinator.  Include the contents of %APPDATA%\\FAM Market Manager\\_fam_update.log if it exists — that file is the updater's own log.",
        ),
        keywords=('update', 'failed', 'pending', 'not complete', 'rollback',
                  'wrong version'),
        related_articles=('pending-update-marker', 'check-for-updates',
                          'manual-install'),
    ),

    TroubleshootingFlow(
        id='ts-instance-lock',
        title='"Another FAM Market Manager instance is already running"',
        symptom='When you try to launch the app, an error dialog refuses to open it',
        steps=(
            "1. Look at your Windows taskbar.  Is FAM Manager already open in another window?  If yes, click that window — you don't need a second copy.",
            "2. If not, open Task Manager (Ctrl + Shift + Esc).  In the Processes tab, look for any line named 'FAM Manager.exe'.",
            "3. If you see one, click it and click 'End task'.  Wait 5 seconds.",
            "4. Try launching FAM Manager again.  This fixes it most of the time.",
            "5. If it still won't launch and Task Manager shows no FAM Manager.exe: open File Explorer, paste %APPDATA%\\FAM Market Manager\\ into the address bar, find the file '.fam_instance.lock', delete it.",
            "6. Launch the app.  The lock file will be re-created automatically.",
            "7. If the message keeps coming back even after step 6, send a diagnostic via Help → System Status → Copy Diagnostic Info.",
        ),
        keywords=('already running', 'instance', 'lock', "won't open",
                  'second copy', 'duplicate'),
        related_articles=('instance-lock-already-running',
                          'where-data-lives'),
    ),

    TroubleshootingFlow(
        id='ts-data-not-on-sheet',
        title="My transactions aren't showing on the shared Google Sheet",
        symptom="The app shows confirmed transactions, but they don't appear on the shared Google Sheet",
        steps=(
            "1. Look at the indicator chip in the title bar.  Green = synced.  Anything else = sync hasn't reached the sheet yet — handle that first (see 'The sync indicator is red' or 'Sync indicator says No network').",
            "2. If the chip is green: force a re-sync.  Settings → Cloud Sync → Sync to Cloud.  Wait for the green confirmation.",
            "3. Refresh your browser's view of the Google Sheet (Ctrl + R in Chrome).",
            "4. Make sure you're on the right tab.  Receipts → 'Detailed Ledger' tab.  Vendor totals → 'Vendor Reimbursement' tab.  FMNP checks → 'FMNP Entries' tab.  Rewards → 'Generated Rewards' tab.",
            "5. Each row has 'market_code' and 'device_id' columns at the far left.  Filter by your laptop's device_id (find it under Help → System Status) to see only your rows.",
            "6. If your market runs multiple laptops: each laptop has a unique device_id, so the same customer label (e.g. C-005) might appear from two different laptops — they're different customers.  Filter by device_id.",
            "7. Still missing?  Help → System Status → Copy Diagnostic Info → email coordinator with the date and the tab you're looking at.",
            "8. Your local data is safe regardless of what's on the sheet.  Re-syncing later cannot lose anything.",
        ),
        keywords=('not showing', 'missing', 'sheet empty', 'rows missing',
                  "can't find", 'not on sheet', 'where is my data'),
        related_articles=('data-not-on-sheet', 'sync-indicator',
                          'sync-overview', 'multi-laptop-deployment'),
    ),

    TroubleshootingFlow(
        id='ts-offline-saturday',
        title='Working a market with no internet at all',
        symptom='Wi-Fi at the market venue is down or you have no signal',
        steps=(
            "1. Keep working.  The app is fully functional offline — Receipt Intake, Payment, FMNP Entry, Adjustments, Reports, even printing receipts.",
            "2. The indicator chip will show gray ('No network').  This is normal offline.  Local data is safe.",
            "3. Sync is automatically deferred until internet returns.  You don't have to do anything.",
            "4. The plain-text ledger backup at %APPDATA%\\FAM Market Manager\\fam_ledger_backup.txt records every confirmed transaction in plain English — open it in Notepad if you want to verify.",
            "5. At end-of-day: close the market day, take the laptop home (or any Wi-Fi), wait a minute, then click 'Sync to Cloud' to push the day's data immediately.",
            "6. Verify the indicator chip turns green and the rows appear on the shared Google Sheet.",
            "7. If sync fails after you reach Wi-Fi, see 'The sync indicator is red'.",
        ),
        keywords=('offline', 'no internet', 'wifi down', 'no signal',
                  'venue', 'works offline'),
        related_articles=('offline-saturday-runbook', 'no-network-data-safe',
                          'backups'),
    ),

    TroubleshootingFlow(
        id='ts-rewards-empty',
        title='Generated Rewards report is empty even though we gave tokens',
        symptom='Reports → Generated Rewards tab shows no rows, but you handed customers tokens during the day',
        steps=(
            "1. Confirm rewards is actually enabled.  Settings → Rewards tab — the master 'Rewards enabled' toggle at top must be checked.",
            "2. Confirm at least one rule is Active.  Each rule has an Active toggle in the rules table.",
            "3. Were the customer's payments using a method that triggers a rule?  E.g. if your rules trigger on SNAP only, a Cash-only order won't generate rewards.",
            "4. Did the customer's payment hit the threshold?  E.g. a $5 SNAP threshold rule won't fire on $4.50 of SNAP.",
            "5. Check Help → System Status — confirm the 'Rewards' line shows enabled and the rule count matches what you expect.",
            "6. If rules look right but the report is empty: Settings → Cloud Sync → Sync to Cloud, then re-check.  The Generated Rewards tab is local; the report is built from confirmed orders.",
            "7. If you're sure tokens were given but the report disagrees, the order was likely voided after rewards fired.  See 'I gave tokens but the order was voided'.",
        ),
        keywords=('rewards', 'empty', 'tokens', 'not showing', 'rules',
                  'generated rewards', 'no rows'),
        related_articles=('rewards-overview', 'rewards-configure',
                          'rewards-given-then-voided'),
    ),

    TroubleshootingFlow(
        id='ts-no-coordinator',
        title='Something is wrong and I cannot reach the coordinator',
        symptom='You need help right now and the project owner / coordinator is not reachable',
        steps=(
            "1. Don't panic.  Local data is almost always fine — the app's safety design means SQLite + ledger + backups all preserve transactions independently.",
            "2. Help → System Status → Copy Diagnostic Info.  Save the text to Notepad (Desktop, name it 'FAM diagnostic <today>.txt') — you'll want it later.",
            "3. Help → Browse — search for the symptom in your own words.  Try synonyms (e.g. 'photo' AND 'picture', 'sync' AND 'upload').",
            "4. Help → Troubleshooting — scan the symptom titles; the closest match is your starting point.",
            "5. If you're seeing an error dialog, take a phone photo of it.  The dialog wording is often the answer to 'what to search for'.",
            "6. The most common true emergencies and their answers: app won't open → 'ts-app-wont-start'.  Update broken → 'ts-update-failed'.  Already-running message → 'ts-instance-lock'.  Data missing → 'restore-from-backup' article.",
            "7. If the workflow is broken (you can't enter receipts), the printed offline runbook in docs/EMERGENCY_RUNBOOK.md (a coordinator should have a printed copy at the booth) covers paper-based fallback.",
            "8. Take notes on what you did.  Tomorrow / next week, send the diagnostic + your notes to the coordinator.  Auditing what happened reproduces correctly later because everything is in the audit log.",
        ),
        keywords=('emergency', 'no help', 'coordinator', 'sean', 'urgent',
                  'unreachable', 'alone', 'panic'),
        related_articles=('diagnostic-info-no-internet', 'where-data-lives',
                          'restore-from-backup', 'offline-saturday-runbook'),
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
