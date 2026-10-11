"""Closing a month: check that everything for it is in, then mark it done."""
import json
import sqlite3

from . import balances, expected, summary


def closed_months(conn: sqlite3.Connection) -> list[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = 'closed_months'").fetchone()
    return json.loads(row[0]) if row else []


def set_closed(conn: sqlite3.Connection, month: str, closed: bool) -> None:
    ms = {m for m in closed_months(conn)}
    (ms.add if closed else ms.discard)(month)
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('closed_months', ?)", (json.dumps(sorted(ms)),))
    conn.commit()


def to_close(conn: sqlite3.Connection, today: str) -> str | None:
    """The last finished month that has data and has not been closed yet."""
    done = set(closed_months(conn))
    cands = [m for m in summary.months_available(conn) if m < today[:7] and m not in done]
    return cands[-1] if cands else None


def checks(conn: sqlite3.Connection, month: str, today: str) -> list[dict]:
    out = []
    cl = expected.checklist(conn, today)
    block = next((b for b in cl["by_month"] if b["key"] == month), None)
    if block:
        missing = [i["label"] for g in block["groups"] for i in g["items"] if i["status"] == "todo" and not i.get("children")]
        out.append({"label": "Statements are in", "ok": not missing,
                    "detail": ("Missing: " + ", ".join(missing)) if missing else "All the statements for this month are uploaded.", "link": "/upload"})
    sort = conn.execute("SELECT COUNT(DISTINCT description_norm) FROM transactions WHERE budget_month = ? AND category_status = 'proposed'", (month,)).fetchone()[0]
    out.append({"label": "Payments are sorted", "ok": sort == 0,
                "detail": f"{sort} merchant(s) still waiting for your approval." if sort else "Every payment has a category you approved.", "link": "/review"})
    drafts = conn.execute("SELECT COUNT(*) FROM batches WHERE status != 'committed'").fetchone()[0]
    out.append({"label": "Imports are submitted", "ok": drafts == 0,
                "detail": f"{drafts} import(s) not submitted yet." if drafts else "Nothing is waiting to be submitted.", "link": "/imports"})
    items = conn.execute("SELECT COUNT(*) FROM discrepancies WHERE status = 'open' AND type != 'manual'").fetchone()[0]
    out.append({"label": "Nothing odd left to check", "ok": items == 0,
                "detail": f"{items} item(s) to check (card payments, fees)." if items else "No open items.", "link": "/items"})
    pend = [p for p in balances.pending(conn, today) if not p["current"] and not p["folded"]]
    out.append({"label": "Balances are updated", "ok": not pend,
                "detail": f"{len(pend)} account(s) need a number." if pend else "Your main accounts are up to date.", "link": "/balances"})
    return out


def is_closed(conn: sqlite3.Connection, month: str | None) -> bool:
    return bool(month) and month in closed_months(conn)
