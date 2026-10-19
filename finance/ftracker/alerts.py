"""Things worth knowing about, worked out from your data. Each alert can be dismissed."""
import json
import sqlite3
from datetime import date, timedelta

from . import budgets, expected, reconcile, recurring, summary
from .charts import money

KEY = "alerts_dismissed"


def dismissed(conn: sqlite3.Connection) -> set[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()
    return set(json.loads(row[0])) if row else set()


def dismiss(conn: sqlite3.Connection, alert_id: str) -> None:
    ids = dismissed(conn) | {alert_id}
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (KEY, json.dumps(sorted(ids)[-300:])))
    conn.commit()


def _latest_data(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT MAX(COALESCE(period_end, billing_date)) FROM statements").fetchone()
    return row[0] or "0000-00-00"


def build(conn: sqlite3.Connection, today: str, include_dismissed: bool = False) -> list[dict]:
    out: list[dict] = []
    now_d = date.fromisoformat(today)
    latest = _latest_data(conn)
    for i in recurring.detect(conn, today):
        if i["debt"]:
            continue
        if i["active"] and i["cadence"] == "monthly" and abs(i["change"]) >= 0.08 and i["prev_amount"] >= 10:
            up = i["change"] > 0
            out.append({"id": f'price:{i["key"]}:{i["last_date"]}', "severity": "warning" if up else "info", "title": f'{i["name"]} {"went up" if up else "went down"}',
                        "detail": f'From {money(i["prev_amount"], "ILS", 2)} to {money(i["last_amount"], "ILS", 2)} a month ({i["change"] * 100:+.0f}%).',
                        "link": f'/?find={i["name"][:20]}'})
        if i["new"] and i["active"]:
            out.append({"id": f'new:{i["key"]}', "severity": "info", "title": f'New recurring charge: {i["name"]}',
                        "detail": f'{money(i["usual"], "ILS", 2)} a month, about {money(i["yearly_cost"])} a year.', "link": "/recurring"})
        if i["cadence"] == "monthly" and i["active"]:
            due = date.fromisoformat(i["next"]) + timedelta(days=6)
            if due <= now_d and due.isoformat() <= latest and date.fromisoformat(i["last_date"]) < now_d - timedelta(days=36):
                out.append({"id": f'missing:{i["key"]}:{i["next"][:7]}', "severity": "warning", "title": f'{i["name"]} did not come this month',
                            "detail": f'It was charged around the {i["last_date"][8:10]}th each month, last on {i["last_date"]}. Cancelled, or not on your statements yet?',
                            "link": "/recurring"})
        if i["cadence"] == "yearly" and i["active"]:
            due = date.fromisoformat(i["next"])
            if 0 <= (due - now_d).days <= 30:
                out.append({"id": f'renew:{i["key"]}:{i["next"]}', "severity": "info", "title": f'{i["name"]} renews soon',
                            "detail": f'About {money(i["usual"])} around {i["next"]}.', "link": "/recurring"})
    # the same payment twice within a few days
    for r in conn.execute(
            """SELECT a.id AS a_id, b.id AS b_id, a.txn_date AS d1, b.txn_date AS d2, a.description AS d, -a.amount AS spent
               FROM transactions a JOIN transactions b ON b.description_norm = a.description_norm AND b.amount = a.amount AND b.id > a.id
                    AND b.account_id = a.account_id AND ABS(julianday(b.txn_date) - julianday(a.txn_date)) <= 2
               JOIN categories c ON c.id = a.category_id
               WHERE a.amount < 0 AND -a.amount >= 40 AND a.txn_date >= ? AND c.neutral = 0 AND c.kind IN ('expense','fee') AND a.kind != 'card_payment'
               ORDER BY a.txn_date DESC LIMIT 6""", ((now_d - timedelta(days=75)).isoformat(),)):
        out.append({"id": f'dup:{r["a_id"]}:{r["b_id"]}', "severity": "warning", "title": f'Charged twice? {r["d"][:30]}',
                    "detail": f'{money(r["spent"], "ILS", 2)} on {r["d1"]} and again on {r["d2"]}.', "link": f'/?find={r["d"][:20]}'})
    late = [f for f in reconcile.fee_status(conn, today) if f["state"] == "overdue"]
    if late:
        total = sum(f["amount"] for f in late)
        out.append({"id": f'fees:{max(f["fee_id"] for f in late)}', "severity": "warning",
                    "title": f'{len(late)} bank fee(s) not refunded, {money(total, "ILS", 2)} in all',
                    "detail": "Fees of this kind were refunded within 14 days before. The oldest is from " + min(f["date"] for f in late) + ". Ask the bank.", "link": "/items"})
    cl = expected.checklist(conn, today)
    missing = [i["label"] for g in cl["groups"] for i in g["items"] if i["status"] == "todo" and not i.get("children")]
    if missing:
        out.append({"id": "statements:" + "|".join(sorted(missing))[:80], "severity": "info", "title": f"{len(missing)} statement(s) still to drop in",
                    "detail": ", ".join(missing[:4]) + ("…" if len(missing) > 4 else ""), "link": "/upload"})
    bs = budgets.get_all(conn)
    months = [m for m in summary.months_available(conn) if m <= today[:7]]
    if bs and months:
        m = months[-1]
        spent: dict[str, float] = {}
        for r in summary.spending_by_category(conn, m):
            spent[r["category"]] = spent.get(r["category"], 0) + r["spent"]
        for cat, limit in bs.items():
            if spent.get(cat, 0) > limit:
                out.append({"id": f"budget:{cat}:{m}", "severity": "warning", "title": f"{cat} is over budget in {m}",
                            "detail": f"{money(spent[cat])} against a budget of {money(limit)}.", "link": f"/?p=month&m={m}&open={cat}"})
    if not include_dismissed:
        gone = dismissed(conn)
        out = [a for a in out if a["id"] not in gone]
    order = {"warning": 0, "info": 1}
    return sorted(out, key=lambda a: order.get(a["severity"], 2))
