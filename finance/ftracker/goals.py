"""Goals: a target amount, by a date or by an age, tracked from your balances.

A goal counts the latest balance of the accounts linked to it (all of them if none are linked,
which makes it a net worth goal). Progress compares what you would need to add each month with
how fast the goal has actually been growing recently, which includes market gains.
"""
import sqlite3
from datetime import date

from . import balances, fx
from .accounts import find_account, owner_id
from .db import audit, now

GROWTH_WINDOW_MONTHS = 6


def _months_between(a: date, b: date) -> float:
    return (b.year - a.year) * 12 + (b.month - a.month) + (b.day - a.day) / 30.0


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:                    # 29 February
        return d.replace(year=d.year + years, day=28)


def set_birth_date(conn: sqlite3.Connection, owner: str, birth: str) -> None:
    date.fromisoformat(birth)
    conn.execute("UPDATE owners SET birth_date = ? WHERE id = ?", (birth, owner_id(conn, owner)))
    conn.commit()


def add_goal(conn: sqlite3.Connection, name: str, target_amount: float, currency: str = "ILS",
             target_date: str | None = None, target_age: int | None = None, owner: str | None = None,
             accounts_: list[str] | None = None, annual_return: float = 0.0, note: str | None = None) -> int:
    if target_amount <= 0:
        raise ValueError("the target amount must be above zero")
    if target_date and target_age:
        raise ValueError("give a target date or a target age, not both")
    if target_date:
        date.fromisoformat(target_date)
    if target_age:
        if not owner:
            raise ValueError("a target age needs an owner, so the app knows whose age")
        if not conn.execute("SELECT birth_date FROM owners WHERE id = ?", (owner_id(conn, owner),)).fetchone()[0]:
            raise ValueError(f"set {owner}'s birth date first: finance owner birth {owner} YYYY-MM-DD")
    cur = conn.execute(
        """INSERT INTO goals (name, target_amount, currency, target_date, target_age, owner_id, annual_return, note, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (name, target_amount, currency.upper(), target_date, target_age, owner_id(conn, owner), annual_return, note, now()))
    for ref in accounts_ or []:
        conn.execute("INSERT OR IGNORE INTO goal_accounts (goal_id, account_id) VALUES (?, ?)",
                     (cur.lastrowid, find_account(conn, ref)["id"]))
    audit(conn, "goal_added", "goal", cur.lastrowid, name)
    conn.commit()
    return cur.lastrowid


def resolve_target_date(conn: sqlite3.Connection, goal) -> date | None:
    if goal["target_date"]:
        return date.fromisoformat(goal["target_date"])
    if goal["target_age"]:
        birth = conn.execute("SELECT birth_date FROM owners WHERE id = ?", (goal["owner_id"],)).fetchone()
        if birth and birth[0]:
            return _add_years(date.fromisoformat(birth[0]), goal["target_age"])
    return None


def _linked(conn, goal_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT account_id, share FROM goal_accounts WHERE goal_id = ?", (goal_id,)).fetchall()


def value_at(conn: sqlite3.Connection, goal, as_of: str) -> float:
    """Goal value in the goal's currency, using the rate on each balance's own date."""
    links = _linked(conn, goal["id"])
    total = 0.0
    if not links:
        owner = None
        if goal["owner_id"]:
            owner = conn.execute("SELECT name FROM owners WHERE id = ?", (goal["owner_id"],)).fetchone()[0]
        return balances.net_worth(conn, as_of, goal["currency"], owner)["total"]
    shares = {l["account_id"]: l["share"] for l in links}
    for r in balances.latest(conn, as_of):
        if r["account_id"] in shares:
            total += shares[r["account_id"]] * fx.convert(conn, r["amount"], r["currency"], goal["currency"], as_of)
    return total


def _snapshot_dates(conn, goal) -> list[str]:
    links = [l["account_id"] for l in _linked(conn, goal["id"])]
    if links:
        marks = ",".join("?" * len(links))
        rows = conn.execute(f"SELECT DISTINCT as_of FROM balances WHERE account_id IN ({marks}) ORDER BY as_of", links)
    else:
        rows = conn.execute("SELECT DISTINCT as_of FROM balances ORDER BY as_of")
    return [r[0] for r in rows]


def recent_growth_per_month(conn: sqlite3.Connection, goal, as_of: str) -> float | None:
    """Average monthly change in the goal's value over the last few months of snapshots."""
    today = date.fromisoformat(as_of)
    dates = [d for d in _snapshot_dates(conn, goal) if d <= as_of]
    if len(dates) < 2:
        return None
    last = date.fromisoformat(dates[-1])
    window = [d for d in dates if _months_between(date.fromisoformat(d), last) <= GROWTH_WINDOW_MONTHS]
    first = date.fromisoformat(window[0] if len(window) >= 2 else dates[-2])
    months = _months_between(first, last)
    if months <= 0:
        return None
    try:
        return (value_at(conn, goal, last.isoformat()) - value_at(conn, goal, first.isoformat())) / months
    except LookupError:
        return None


def required_monthly(current: float, target: float, months: float, annual_return: float) -> float:
    remaining = target - current
    if months <= 0:
        return max(remaining, 0.0)
    if annual_return <= 0:
        return max(remaining / months, 0.0)
    r = (1 + annual_return) ** (1 / 12) - 1
    growth = (1 + r) ** months
    need = target - current * growth
    return max(need * r / (growth - 1), 0.0)


def months_to_reach(current: float, target: float, monthly: float, annual_return: float = 0.0) -> float | None:
    if current >= target:
        return 0.0
    if monthly <= 0 and annual_return <= 0:
        return None
    r = (1 + annual_return) ** (1 / 12) - 1 if annual_return > 0 else 0.0
    value, months = current, 0
    while value < target:
        value = value * (1 + r) + monthly
        months += 1
        if months > 1200:
            return None
    return float(months)


def progress(conn: sqlite3.Connection, goal_id: int, today: str | None = None, extra_monthly: float = 0.0) -> dict:
    today = today or date.today().isoformat()
    goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        raise ValueError(f"no goal #{goal_id}")
    out = {"id": goal["id"], "name": goal["name"], "target": goal["target_amount"], "currency": goal["currency"],
           "as_of": today, "status": "no_data", "current": None, "percent": None, "target_date": None,
           "months_left": None, "required_per_month": None, "growth_per_month": None, "projected_date": None,
           "gap_per_month": None}
    try:
        current = value_at(conn, goal, today)
    except LookupError as exc:
        out["status"], out["note"] = "needs_fx", str(exc)
        return out
    out["current"], out["percent"] = current, min(current / goal["target_amount"], 9.99)
    target_date = resolve_target_date(conn, goal)
    growth = recent_growth_per_month(conn, goal, today)
    out["growth_per_month"] = growth
    if current >= goal["target_amount"]:
        out["status"] = "reached"
        return out
    pace = (growth or 0.0) + extra_monthly
    reach = months_to_reach(current, goal["target_amount"], pace, 0.0)
    if reach is not None:
        y, m = divmod(date.fromisoformat(today).month - 1 + int(reach), 12)
        out["projected_date"] = date(date.fromisoformat(today).year + y, m + 1, 1).isoformat()
    if target_date:
        months_left = max(_months_between(date.fromisoformat(today), target_date), 0.0)
        out["target_date"], out["months_left"] = target_date.isoformat(), months_left
        out["required_per_month"] = required_monthly(current, goal["target_amount"], months_left, goal["annual_return"])
        if growth is None and not extra_monthly:
            out["status"] = "no_data"
        else:
            out["gap_per_month"] = max(out["required_per_month"] - pace, 0.0)
            out["status"] = "on_track" if out["gap_per_month"] <= 0.5 else "behind"
    else:
        out["status"] = "tracking" if growth is not None else "no_data"
    return out


def all_progress(conn: sqlite3.Connection, today: str | None = None) -> list[dict]:
    return [progress(conn, g["id"], today) for g in conn.execute("SELECT id FROM goals WHERE active = 1 ORDER BY id")]
