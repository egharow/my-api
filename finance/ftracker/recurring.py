"""Payments that repeat: subscriptions, bills and the like, found from your own history.

A merchant is monthly when it is charged in at least three months, about once a month, for about the same amount.
It is yearly when the same amount comes back about a year later.
"""
import sqlite3
from datetime import date, timedelta
from statistics import median

from .normalize import merchant_key

WINDOW_MONTHS = 16


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, min(d.day, 28))


def _lines(conn: sqlite3.Connection, today: str):
    since = (date.fromisoformat(today) - timedelta(days=WINDOW_MONTHS * 31)).isoformat()
    return conn.execute(
        """SELECT t.txn_date, t.description, t.description_norm, -t.amount AS spent, c.name AS category, c.kind AS ckind
           FROM transactions t JOIN categories c ON c.id = t.category_id
           WHERE c.neutral = 0 AND c.kind IN ('expense','debt') AND t.kind != 'card_payment' AND t.currency = 'ILS'
             AND t.left_out = 0 AND t.amount < 0 AND t.txn_date >= ? ORDER BY t.txn_date""", (since,)).fetchall()


def _index(m: str) -> int:
    return int(m[:4]) * 12 + int(m[5:7])


def _steady_run(months: list[str], by_month: dict) -> list[str] | None:
    """The latest stretch of at least three months in a row with about one charge of about the same size."""
    best: list[str] | None = None
    run: list[str] = []
    for m in months:
        if run and _index(m) != _index(run[-1]) + 1:
            run = []
        run.append(m)
        window = run
        while len(window) >= 3:
            sums = [sum(by_month[x]) for x in window]
            med = median(sums)
            if med >= 5 and all(len(by_month[x]) <= 2 for x in window) and all(abs(v - med) <= 0.2 * med for v in sums):
                best = list(window)
                break
            window = window[1:]
    return best


def detect(conn: sqlite3.Connection, today: str) -> list[dict]:
    groups: dict[str, list] = {}
    for r in _lines(conn, today):
        groups.setdefault(merchant_key(r["description"]) or r["description_norm"], []).append(r)
    now_d = date.fromisoformat(today)
    out = []
    for key, rows in groups.items():
        by_month: dict[str, list[float]] = {}
        for r in rows:
            by_month.setdefault(r["txn_date"][:7], []).append(r["spent"])
        months = sorted(by_month)
        last = rows[-1]
        name = max({r["description"] for r in rows}, key=lambda d: sum(1 for r in rows if r["description"] == d))
        item = None
        run = _steady_run(months, by_month)
        if run:
            sums = [sum(by_month[m]) for m in run]
            med = median(sums)
            allsums = [sum(by_month[m]) for m in months]
            prev = median(sums[:-1]) if len(sums) > 1 else med
            item = {"cadence": "monthly", "usual": round(med, 2),
                    "yearly_cost": round(sum(allsums[-12:]) if len(allsums) >= 12 else sum(allsums) / len(allsums) * 12, 2),
                    "last_amount": round(sum(by_month[months[-1]]), 2), "prev_amount": round(prev, 2),
                    "next": _add_months(date.fromisoformat(last["txn_date"]), 1).isoformat(),
                    "active": date.fromisoformat(last["txn_date"]) >= now_d - timedelta(days=50),
                    "recent_months": months[-3:], "new": len(months) <= 4 and date.fromisoformat(rows[0]["txn_date"]) >= now_d - timedelta(days=130)}
        if item is None and len(rows) >= 2:
            dates = [date.fromisoformat(r["txn_date"]) for r in rows]
            yearly = [(a, b) for a, b in zip(rows, rows[1:]) if 335 <= (date.fromisoformat(b["txn_date"]) - date.fromisoformat(a["txn_date"])).days <= 395
                      and abs(b["spent"] - a["spent"]) <= 0.25 * max(a["spent"], 1)]
            if yearly and last["spent"] >= 30:
                item = {"cadence": "yearly", "usual": round(last["spent"], 2), "yearly_cost": round(last["spent"], 2), "last_amount": round(last["spent"], 2),
                        "prev_amount": round(yearly[-1][0]["spent"], 2), "next": _add_months(dates[-1], 12).isoformat(),
                        "active": dates[-1] >= now_d - timedelta(days=395), "recent_months": months[-1:], "new": False}
        if item:
            change = (item["last_amount"] - item["prev_amount"]) / item["prev_amount"] if item["prev_amount"] else 0.0
            item.update({"key": key, "name": name, "category": last["category"], "debt": last["ckind"] == "debt", "last_date": last["txn_date"],
                         "change": round(change, 3), "norms": sorted({r["description_norm"] for r in rows})})
            out.append(item)
    return sorted(out, key=lambda i: (-int(i["active"]), -i["yearly_cost"]))


def fixed_merchants(conn: sqlite3.Connection, today: str | None = None) -> set[str]:
    """The description_norm values of merchants that are monthly bills, so a trip does not count them."""
    today = today or date.today().isoformat()
    return {n for i in detect(conn, today) if i["cadence"] == "monthly" and i["active"] for n in i["norms"]}
