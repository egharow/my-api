"""Things worth knowing without digging: costs that repeat every month, and payments that stand out."""
import sqlite3
from statistics import median

from . import summary
from .normalize import merchant_key

WINDOW_MONTHS = 6
MIN_MONTHS = 3
MAX_SPREAD = 0.25          # amounts of a fixed cost stay within 25% of their usual size
CHANGED = 0.10             # flag a fixed cost that changed by more than 10%
BIG = 1000.0
NEW_MERCHANT_MIN = 500.0
OUTLIER_FACTOR = 3.0


def _lines(conn: sqlite3.Connection, months: list[str], owner: str | None):
    where = ["c.neutral = 0", "c.kind IN ('expense','fee','debt')", "t.kind != 'card_payment'", "t.currency = 'ILS'", "t.amount < 0",
             f"t.budget_month IN ({','.join('?' * len(months))})"]
    args: list = list(months)
    summary.apply_oneoff(conn, where, args)
    if not summary.include_reimbursed(conn):
        where.append("c.reimbursed = 0")
    if owner:
        where.append("o.name = ?")
        args.append(owner)
    return conn.execute(
        f"""SELECT t.id, t.txn_date, t.budget_month AS m, t.description, -t.amount AS spent, COALESCE(p.name, c.name) AS category, a.label AS account
            FROM transactions t JOIN categories c ON c.id = t.category_id LEFT JOIN categories p ON p.id = c.parent_id
            JOIN accounts a ON a.id = t.account_id LEFT JOIN owners o ON o.id = a.owner_id
            WHERE {' AND '.join(where)} ORDER BY t.txn_date""", args).fetchall()


def _window(conn: sqlite3.Connection, month: str, data_months: list[str]) -> list[str]:
    return [m for m in data_months if m <= month][-WINDOW_MONTHS:]


def fixed_costs(conn: sqlite3.Connection, month: str, data_months: list[str], owner: str | None = None) -> list[dict]:
    """Costs that come every month at about the same amount, with this month's amount and whether it changed."""
    win = _window(conn, month, data_months)
    if len(win) < MIN_MONTHS:
        return []
    by: dict[str, dict] = {}
    for r in _lines(conn, win, owner):
        key = merchant_key(r["description"]) or r["description"]
        d = by.setdefault(key, {"name": r["description"], "category": r["category"], "months": {}})
        d["months"][r["m"]] = d["months"].get(r["m"], 0) + r["spent"]
    out = []
    for key, d in by.items():
        amounts = [v for v in d["months"].values()]
        if len(d["months"]) < MIN_MONTHS:
            continue
        usual = median(amounts)
        if usual < 30 or max(abs(a - usual) for a in amounts) > usual * MAX_SPREAD:
            continue
        out.append({"name": d["name"], "category": d["category"], "usual": round(usual, 2), "this": d["months"].get(month),
                    "months": len(d["months"])})
    if not owner:                                              # mortgage and loans that only exist in your old sheet
        sheet: dict[str, dict[str, float]] = {}
        for e in summary.debt_entries(conn, set(win)):
            sheet.setdefault(summary._debt_label(e["label"]), {})[e["month"]] = sheet.get(summary._debt_label(e["label"]), {}).get(e["month"], 0) + e["actual"]
        have = {o["category"] for o in out}
        for name, per in sheet.items():
            if name not in have and len(per) >= MIN_MONTHS:
                this = per.get(month)
                if this is None:                                   # the month may be on a bank statement instead of the sheet
                    this = next((r["spent"] for r in summary.spending_by_category(conn, month) if r["category"] == name), None)
                out.append({"name": name, "category": name, "usual": round(median(per.values()), 2), "this": this, "months": len(per)})
    for o in out:
        if o["this"] is None:
            o["status"] = "not yet" if month >= data_months[-1] else "missing"
        elif abs(o["this"] - o["usual"]) > o["usual"] * CHANGED:
            o["status"] = "changed"
        else:
            o["status"] = "ok"
    return sorted(out, key=lambda o: -o["usual"])[:14]


def unusual(conn: sqlite3.Connection, month: str, data_months: list[str], owner: str | None = None, fixed: list[dict] | None = None) -> list[dict]:
    """Payments this month that stand out: very large, far above that category's usual line, or from a merchant you've never paid."""
    prior = [m for m in data_months if m < month][-12:]
    fixed_names = {merchant_key(f["name"]) for f in (fixed or [])}
    seen: set[str] = set()
    cat_sizes: dict[str, list[float]] = {}
    for r in _lines(conn, prior, owner) if prior else []:
        seen.add(merchant_key(r["description"]) or r["description"])
        cat_sizes.setdefault(r["category"], []).append(r["spent"])
    out = []
    for r in _lines(conn, [month], owner):
        key = merchant_key(r["description"]) or r["description"]
        if key in fixed_names or r["category"] in ("Mortgage", "Loans"):
            continue
        reasons = []
        if r["spent"] >= BIG:
            reasons.append("a large payment")
        sizes = cat_sizes.get(r["category"])
        if r["category"] == "Uncategorised":
            reasons.append("has no category yet")
        elif sizes and len(sizes) >= 5 and r["spent"] >= 300 and r["spent"] > OUTLIER_FACTOR * median(sizes):
            reasons.append(f"much more than a usual {r['category']} payment")
        if prior and key not in seen and r["spent"] >= NEW_MERCHANT_MIN:
            reasons.append("a merchant you haven't paid before")
        if reasons:
            out.append({"id": r["id"], "date": r["txn_date"], "description": r["description"], "amount": r["spent"],
                        "category": r["category"], "account": r["account"], "why": " · ".join(reasons[:2])})
    return sorted(out, key=lambda o: -o["amount"])[:8]
