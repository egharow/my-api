# Household finance tracker (local)

Statements go in, categorised and reconciled numbers come out. Everything runs on your own
computer; the database, statements and backups never leave it and are never committed to git.

## Status

Built and tested (42 automated tests, run on Linux with invented data; not yet run on Windows):

- Parsers for the Isracard `.xlsx`, Amex `.xls` (several cards per file) and Leumi account `.pdf`.
- Import into a draft, de-duplicating by voucher number / balance so overlapping files are safe.
- Originals filed under `archive/` by type and card; unrecognised files set aside with a reason.
- Category proposals you approve or change; approvals can become rules; large one-offs go to review.
- Reconciliation of card payments against card statements, and a running-balance check.
- Bank-fee refund tracking: flagged when a fee is not refunded within 14 days.
- "Expected files" checklist, comments on every discrepancy, Submit with a timestamp, reopen with a reason.
- ₪/$ rates by date, balances you enter, net worth in either currency.
- One-time import of the old budget sheet (`finance sheet-import`): 18 monthly logs, income/savings/debt/budget
  figures, net worth snapshots and loans. Your old categories are kept and become rules for new statements.

Not built yet: the browser dashboard, goals, Google Sheet sync.
Live Bank of Israel rate download is written but untested; use `finance fx set` meanwhile.

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
4. Dollar balances need a rate for each snapshot date: `finance fx needed` lists them; enter each with
   `finance fx set 2024-05-10 USD ILS 3.65` (or try `finance fx fetch --start 2024-05-01 --end 2026-04-30`).
5. `finance review`, then `finance submit N`.

## Each time

1. Drop the new files into `inbox\`.
2. Double-click `import.bat`. Only new files are read; history is not reloaded.
3. Review, then submit (below). `status.bat` shows the checklist at any time.

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
finance fx needed | set DATE USD ILS RATE | fetch --start D --end D
finance sheet-import FILE.xlsx [--save] [--map SHEET=APP] [--link SHEET_ACCOUNT=ACCOUNT]
finance account destination-rule "העברה דיגיטל" --bank "One Zero"
finance submit N [--ack]               lock import N; open items are recorded if you acknowledge
finance reopen N --reason "..."        amend a submitted import (kept as a new version)
finance history
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
