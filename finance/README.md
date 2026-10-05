# Household finance tracker (local)

Statements go in, categorised and reconciled numbers come out. Everything runs on your own
computer; the database, statements and backups never leave it and are never committed to git.

## Status

Built and tested (102 automated tests, run on Linux with invented data; not yet run on Windows):

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

## Setup (Windows)

1. Install Python 3.10+ from python.org (tick "Add python to PATH").
2. Double-click `setup.bat`. It creates `%USERPROFILE%\Finance\` with `inbox`, `archive`, `data`, `backups`.
   Keep that folder out of OneDrive/Drive sync: syncing a live database file can corrupt it.
   Put copies of `archive` and `backups` there if you want cloud protection.

## One-time: import the old sheet

1. In Google Sheets: File > Download > Microsoft Excel (.xlsx).
2. Preview (writes nothing): `finance sheet-import "E&S Budget.xlsx"`
3. Check the mapping and warnings, then save as a draft: `finance sheet-import "E&S Budget.xlsx" --save`
   Use `--map "Vituri=Medical"` to map a sheet category you want merged into an existing one.
4. Dollar accounts use today's rate. It is fetched automatically; if you are offline, type it on the
   Balances page or run `finance fx set today USD ILS 3.6`.
5. `finance review`, then `finance submit N`.

## Each time

1. Drop the new files into `inbox\`.
2. Double-click `import.bat`. Only new files are read; history is not reloaded.
3. Double-click `dashboard.bat` to review categories, comment on items, enter balances and submit.
   (`status.bat` shows the checklist in a plain window.)

## First-run rules for your income

```
finance rule add "פאפאיה" Salary --kind income --direction in
finance rule add "מטריקס" "Former employer" --kind income --direction in
finance rule add "מופ\"ת מילואי" "Reserve duty pay" --kind income --direction in
finance account add --kind savings --issuer onezero --label "One Zero" --owner Ely
finance account destination-rule "העברה דיגיטל" --bank "One Zero"
```

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
