"""The checklist of files you should have uploaded, worked out from what the app already knows."""
import sqlite3
from collections import Counter
from datetime import date, timedelta

from . import balances

BANK_STALE_DAYS = 35


def _months_between(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def expected_files(conn: sqlite3.Connection, today: str) -> list[dict]:
    now_d = date.fromisoformat(today)
    items: list[dict] = []

    for card in conn.execute("SELECT * FROM accounts WHERE kind = 'card' AND active = 1"):
        stmts = [date.fromisoformat(r[0]) for r in conn.execute(
            "SELECT billing_date FROM statements WHERE account_id = ? AND billing_date IS NOT NULL", (card["id"],))]
        if not stmts:
            continue
        day = Counter(d.day for d in stmts).most_common(1)[0][0]
        covered = {r[0] for r in conn.execute("SELECT month FROM coverage WHERE account_id = ?", (card["id"],))}
        have = {(d.year, d.month) for d in stmts}
        for y, m in _months_between(min(stmts), now_d):
            if (y, m) in have or f"{y:04d}-{m:02d}" in covered:
                continue
            due = date(y, m, min(day, 28))
            if due >= now_d:
                continue
            late = (now_d - due).days > 3
            items.append({"source": card["label"], "period": f"{y:04d}-{m:02d}",
                          "status": "missing" if late else "due",
                          "detail": f"statement billed around {due.isoformat()}"})

    for bank in conn.execute("SELECT * FROM accounts WHERE kind = 'bank' AND active = 1"):
        spans = sorted((r["period_start"], r["period_end"]) for r in conn.execute(
            "SELECT period_start, period_end FROM statements WHERE account_id = ? AND period_start IS NOT NULL", (bank["id"],)))
        if not spans:
            continue
        for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
            if next_start > prev_end and (date.fromisoformat(next_start) - date.fromisoformat(prev_end)).days > 1:
                items.append({"source": bank["label"], "period": f"{prev_end} to {next_start}",
                              "status": "missing", "detail": "gap between two bank statements"})
        last_end = date.fromisoformat(max(e for _, e in spans))
        if (now_d - last_end).days > BANK_STALE_DAYS:
            items.append({"source": bank["label"], "period": f"after {last_end.isoformat()}",
                          "status": "due", "detail": f"latest statement ends {last_end.isoformat()}"})

    due, last = balances.balances_due(conn, today)
    if due:
        items.append({"source": "Balances", "period": "now", "status": "due",
                      "detail": f"last updated {last}" if last else "never entered"})
    for r in balances.stale(conn, today):
        items.append({"source": r["label"], "period": r["as_of"], "status": "stale",
                      "detail": f"balance last updated {r['as_of']}"})

    # Anything the bank paid that has no statement is raised as a discrepancy; list it here too.
    for r in conn.execute("SELECT title FROM discrepancies WHERE type = 'missing_statement' AND status = 'open'"):
        items.append({"source": "Card statement", "period": "", "status": "missing", "detail": r["title"]})
    return items
