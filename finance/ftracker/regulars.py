"""Things you expect every month: child allowance, a salary, rent, a subscription.

You list them (or accept the app's suggestions). Each month the app checks each one: did it arrive, is the amount what
it usually is, or is it missing. Amounts are compared with what you gave, or with the usual amount from earlier months.
"""
import sqlite3
from datetime import date
from statistics import median

from .db import audit, now
from .normalize import merchant_key, norm_description

WINDOW = 6                 # earlier months that define "usual"
GRACE_DAYS = 5             # it can come a few days after its usual day before it counts as missing
MIN_ABS = 5.0              # differences under five shekels never count as "different"


def items(conn: sqlite3.Connection):
    return conn.execute("SELECT * FROM expected_items ORDER BY direction DESC, name").fetchall()


def add(conn: sqlite3.Connection, name: str, pattern: str, direction: str, amount: float | None = None, tolerance: float = 0.08) -> int:
    pattern = norm_description(pattern or name)
    name = (name or pattern).strip()
    if not pattern:
        raise ValueError("Type the words that appear on the payment, for example קצבת ילדים.")
    if direction not in ("in", "out"):
        raise ValueError("Choose money coming in or money going out.")
    if amount is not None and amount <= 0:
        raise ValueError("The usual amount must be more than zero, or leave it empty to use your history.")
    if not 0.0 <= tolerance <= 1.0:
        raise ValueError("The allowed difference must be between 0 and 100%.")
    if conn.execute("SELECT 1 FROM expected_items WHERE pattern = ? AND direction = ?", (pattern, direction)).fetchone():
        raise ValueError("You already track that.")
    cur = conn.execute("INSERT INTO expected_items (name, pattern, direction, amount, tolerance, created_at) VALUES (?,?,?,?,?,?)",
                       (name, pattern, direction, amount, tolerance, now()))
    audit(conn, "expected_item_added", "expected_item", cur.lastrowid, f"{name} ({direction})")
    return cur.lastrowid


def remove(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute("DELETE FROM expected_items WHERE id = ?", (item_id,))


def _month_lines(conn: sqlite3.Connection, item, months: list[str]):
    sign = "t.amount > 0" if item["direction"] == "in" else "t.amount < 0"
    marks = ",".join("?" * len(months))
    return conn.execute(
        f"""SELECT t.budget_month AS m, t.txn_date, ABS(t.amount) AS amt FROM transactions t
            WHERE t.description_norm LIKE ? AND {sign} AND t.left_out = 0 AND t.kind NOT IN ('card_payment') AND t.budget_month IN ({marks})
            ORDER BY t.txn_date""", [f"%{item['pattern']}%", *months]).fetchall()


def _prev_months(month: str, n: int) -> list[str]:
    y, m = int(month[:4]), int(month[5:7])
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y:04d}-{m:02d}")
    return out


def check(conn: sqlite3.Connection, item, month: str, today: str) -> dict:
    """One item, one month: usual amount, what came, and whether that is fine."""
    prior = _prev_months(month, WINDOW)
    hist = _month_lines(conn, item, prior)
    per: dict[str, float] = {}
    days: list[int] = []
    for r in hist:
        per[r["m"]] = per.get(r["m"], 0.0) + r["amt"]
        days.append(int(r["txn_date"][8:10]))
    usual = item["amount"] if item["amount"] else (round(median(per.values()), 2) if per else None)
    day = int(median(days)) if days else None
    cur = _month_lines(conn, item, [month])
    got = round(sum(r["amt"] for r in cur), 2) if cur else None
    status, diff = "ok", 0.0
    if got is None:
        if month < today[:7]:
            status = "missing"
        else:
            due = (day or 1) + GRACE_DAYS
            status = "missing" if int(today[8:10]) > due and month == today[:7] else "not yet"
        if not per and not item["amount"]:
            status = "no history"
    elif usual:
        diff = round(got - usual, 2)
        if abs(diff) > max(usual * item["tolerance"], MIN_ABS):
            status = "different"
    return {"item": item, "month": month, "usual": usual, "this": got, "day": day, "status": status, "diff": diff, "lines": len(cur)}


def evaluate(conn: sqlite3.Connection, today: str) -> list[dict]:
    """Every enabled item for this month and last month. Last month only matters if it is a problem."""
    out = []
    this = today[:7]
    last = _prev_months(this, 1)[0]
    for it in items(conn):
        if not it["enabled"]:
            continue
        cur = check(conn, it, this, today)
        out.append(cur)
        prev = check(conn, it, last, today)
        if prev["status"] in ("missing", "different"):
            out.append(prev)
    return out


def suggestions(conn: sqlite3.Connection, month: str, data_months: list[str], owner: str | None = None) -> list[dict]:
    """Regular items the app can see in your data that you do not track yet."""
    from . import insights
    tracked = {(i["pattern"], i["direction"]) for i in items(conn)}
    found = []
    for o in insights.fixed_income(conn, month, data_months, owner):
        found.append({"name": o["name"], "direction": "in", "usual": o["usual"], "months": o["months"]})
    for o in insights.fixed_costs(conn, month, data_months, owner):
        found.append({"name": o["name"], "direction": "out", "usual": o["usual"], "months": o["months"]})
    out = []
    for f in found:
        pat = norm_description(merchant_key(f["name"]) or f["name"])
        if pat and (pat, f["direction"]) not in tracked:
            out.append({**f, "pattern": pat})
    return out
