"""Monthly budgets per category, compared with what was spent."""
import sqlite3

from . import summary


def get_all(conn: sqlite3.Connection) -> dict[str, float]:
    return {r[0]: r[1] for r in conn.execute("SELECT category, monthly FROM budgets ORDER BY monthly DESC")}


def save(conn: sqlite3.Connection, amounts: dict[str, float]) -> int:
    """Replace the budgets with these amounts (zero or empty removes one)."""
    conn.execute("DELETE FROM budgets")
    n = 0
    for cat, amount in amounts.items():
        if amount and amount > 0:
            conn.execute("INSERT INTO budgets (category, monthly) VALUES (?, ?)", (cat, round(amount, 2)))
            n += 1
    conn.commit()
    return n


def usual(conn: sqlite3.Connection, months: list[str], owner: str | None = None) -> dict[str, float]:
    """Average monthly spending per category over these months (the ones with data)."""
    tot: dict[str, float] = {}
    have = 0
    for m in months:
        rows = summary.spending_by_category(conn, m, owner)
        if rows:
            have += 1
            for r in rows:
                tot[r["category"]] = tot.get(r["category"], 0.0) + r["spent"]
    return {c: v / have for c, v in tot.items()} if have else {}


def compare(budgets: dict[str, float], spent: dict[str, float], months: int = 1) -> list[dict]:
    """Rows for the categories that have a budget: budget, spent, and what is left or over (largest budgets first)."""
    rows = []
    for cat, monthly in budgets.items():
        b = monthly * months
        s = spent.get(cat, 0.0)
        rows.append({"category": cat, "budget": b, "spent": s, "left": b - s, "over": s > b})
    return sorted(rows, key=lambda r: -r["budget"])
