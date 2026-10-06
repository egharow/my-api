# Household finance tracker (local)

Statements go in, categorised and reconciled numbers come out. Everything runs on your own
computer; the database, statements and backups never leave it and are never committed to git.

## Status

Built and tested (124 automated tests, run on Linux with invented data; not yet run on Windows):

- Parsers for the Isracard `.xlsx`, Amex `.xls` (several cards per file) and Leumi account `.pdf`.
- Import into a draft, de-duplicating by voucher number / balance so overlapping files are safe.
- Originals filed under `archive/` by type and card; unrecognised files set aside with a reason.
- Category proposals you approve or change; approvals can become rules; large one-offs go to review.
- Reconciliation of card payments against card statements, and a running-balance check.
- Bank-fee refund tracking: flagged when a fee is not refunded within 14 days.
- "Expected files" checklist, comments on every discrepancy, Submit with a timestamp, reopen with a reason.
- Balances you enter (due every two months) and net worth in shekels or dollars. Dollar assets use the
  **current** dollar rate (fetched automatically, or typed). Expenses are never converted: cards charge shekels.
- The בהצדעה member card needs no statement: its bank payments simply count as spending.
- One-time import of the old budget sheet; your old categories become rules for new statements.
- Goals by date or by age, with required monthly saving, recent growth and a projection.
- Local dashboard (`finance serve`): net worth, income vs spending, categories, goals, review, comments,
  imports/submit, balances. Works on a phone-width screen and in dark mode.
- Google Sheet kept up to date automatically: after a one-time link, every Submit pushes the submitted
  numbers to the sheet (comments only if you opt in). `finance export` still makes a file if you want one.

Not built: the Max/בהצדעה and Shir's One Zero card parsers (they need sample statements).

Written and tested only against stand-ins, because the build sandbox blocks the internet: the automatic
dollar-rate fetch and the Google Sheet link. If either misbehaves for you, the app says why and keeps working;
type the rate (`finance fx set today USD ILS 3.6`) and tell me what the message said.

## Use it (Windows)

1. Install Python 3.10+ from python.org (tick "Add python to PATH").
2. Unzip the app anywhere (not in OneDrive) and double-click **`Finance.pyw`**. The first run sets itself up
   with a progress window; after that it opens the app in its own Edge window and closes itself when you
   close that window.
3. Everything else happens inside the app: drag statements onto the drop box, review categories, comment on
   items, enter balances, submit, link the Google Sheet, manage people and rules (Setup tab), import the old
   budget sheet (Setup > Import the old budget sheet).

If a `starter_rules.json` and a `seed/` folder (history.xlsx, statements/) sit next to `Finance.pyw`, they are
loaded once on first launch. They are private and never committed to git.

Data lives in the `my-data` folder next to `app` (older versions used `C:\Users\<you>\Finance`, which is copied over on first start; override with FINANCE_HOME): `data` (database), `archive` (originals,
filed by card/account), `backups` (before every import), `app-log.txt`.

## Setup without typing commands

The Setup tab manages people (and the names printed on their statements), who owns each account, and rules
(spending, income, transfers to your household or to a savings account). `starter_rules.json` can pre-fill all of it.

## Sharing

- **Google Sheet (automatic):** open the **Google Sheet** page of the dashboard and follow the 5 steps once
  (paste a small script into the sheet, deploy it, paste its address back). After that every Submit updates the
  sheet by itself; only submitted numbers are sent, and comments only if you tick the box. Command line:
  `finance sheet setup`, `finance sheet link ADDRESS`, `finance sheet sync`, `finance sheet status`.
  Prefer a file? `finance export` writes `exports\finance-summary-DATE.xlsx`.
- **Another device at home:** `finance serve --lan --pin 123456`, then open `http://<this computer>:8765/`
  there. Plain HTTP on your home network only. Each person picks their name at the top; comments use it.

## Commands

```
finance import                         read inbox, create a draft, show the checklist
finance status                         expected files, fee refunds, open items
finance review                         transactions with only a proposed category
finance approve --merchant TEXT --category NAME [--learn]
finance rule add PATTERN CATEGORY [--kind income --direction in]
finance items [--threads]              discrepancies and their comment threads
finance comment ID "text" [--status explained --follow-up 2026-12-01] --author Ely
finance note TXN_ID "text"             start a thread on any transaction
finance balance set "Pension" 123456 --as-of 2026-10-01 [--currency USD]
finance networth [--currency USD]
finance fx show | refresh | set today USD ILS RATE | mode current|historical
finance sheet setup | link ADDRESS | sync | notes on|off | unlink | status
finance sheet-import FILE.xlsx [--save] [--map SHEET=APP] [--link SHEET_ACCOUNT=ACCOUNT]
finance account destination-rule "העברה דיגיטל" --bank "One Zero"
finance submit N [--ack]               lock import N; open items are recorded if you acknowledge
finance reopen N --reason "..."        amend a submitted import (kept as a new version)
finance history
finance serve [--lan --pin N]           the dashboard
finance export [--notes]               workbook for the shared sheet
finance goal add --name N --amount A (--by DATE | --age 40 --owner Ely) [--account LABEL] [--return-rate 0.05]
finance goal list | progress --goal N [--extra 500]
finance owner birth Ely 1990-06-15
finance summary 2026-10 [--owner Shir]
```

Cards book into the month they are billed (the purchase date is kept). An instalment books only
its monthly charge. Card payments and transfers between your own accounts are neutral, so
nothing is counted twice.

## Develop

```
pip install -e ".[dev]" && pytest
```
Tests use invented data only. Never commit real statements.
