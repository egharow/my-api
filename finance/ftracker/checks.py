"""Checking that expenses really happened.

This is separate from approving a category. A line is *unchecked* until someone says it happened (ok) or says
they do not recognise it (question). Only spending counts here: lines in categories that count as spending, not
card payments, moves between your accounts, savings, or lines you left out of the totals.
"""
import sqlite3

from .db import audit, now

REASONS = {
    "not_mine": "I did not make this payment",
    "duplicate": "It looks like a duplicate",
    "wrong_amount": "The amount looks wrong",
    "ask": "I need to ask someone",
}
SPEND = "c.neutral = 0 AND c.kind IN ('expense','fee','debt') AND t.kind != 'card_payment' AND t.left_out = 0"
SELECT = """SELECT t.id, t.txn_date, t.budget_month, t.description, t.detail, t.amount, t.currency, t.category_status, t.check_state,
                   t.check_by, t.check_at, t.check_reason, t.check_note, t.instalment_no, t.instalment_total,
                   c.name AS cat, a.label AS account, a.id AS account_id
            FROM transactions t JOIN categories c ON c.id = t.category_id JOIN accounts a ON a.id = t.account_id"""


def months(conn: sqlite3.Connection) -> list[dict]:
    """Every month with spending, newest first, with how many lines are checked, questioned and still open."""
    rows = conn.execute(
        f"""SELECT t.budget_month AS month, COUNT(*) AS total,
                   SUM(CASE WHEN t.check_state = 'ok' THEN 1 ELSE 0 END) AS ok,
                   SUM(CASE WHEN t.check_state = 'question' THEN 1 ELSE 0 END) AS question
            FROM transactions t JOIN categories c ON c.id = t.category_id
            WHERE {SPEND} GROUP BY t.budget_month ORDER BY t.budget_month DESC""").fetchall()
    return [{"month": r["month"], "total": r["total"], "ok": r["ok"] or 0, "question": r["question"] or 0,
             "todo": r["total"] - (r["ok"] or 0) - (r["question"] or 0)} for r in rows]


def default_month(conn: sqlite3.Connection) -> str | None:
    m = months(conn)
    return m[0]["month"] if m else None


def counts(conn: sqlite3.Connection, month: str) -> dict:
    for m in months(conn):
        if m["month"] == month:
            return m
    return {"month": month, "total": 0, "ok": 0, "question": 0, "todo": 0}


def lines(conn: sqlite3.Connection, month: str | None, state: str = "todo", account: int | None = None, category: str = "",
          sort: str = "date", limit: int = 600):
    where, args = [SPEND], []
    if month:
        where.append("t.budget_month = ?"); args.append(month)
    if state == "todo":
        where.append("t.check_state IS NULL")
    elif state in ("ok", "question"):
        where.append("t.check_state = ?"); args.append(state)
    if account:
        where.append("t.account_id = ?"); args.append(account)
    if category:
        where.append("c.name = ?"); args.append(category)
    order = "ABS(t.amount) DESC, t.id" if sort == "amount" else "t.txn_date DESC, t.id DESC"
    return conn.execute(f"{SELECT} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?", args + [limit]).fetchall()


def flagged(conn: sqlite3.Connection):
    """Every questioned line, in any month, newest first."""
    return conn.execute(f"{SELECT} WHERE {SPEND} AND t.check_state = 'question' ORDER BY t.txn_date DESC, t.id DESC").fetchall()


def todo_count(conn: sqlite3.Connection, month: str | None = None) -> int:
    month = month or default_month(conn)
    return counts(conn, month)["todo"] if month else 0


def flagged_count(conn: sqlite3.Connection) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM transactions t JOIN categories c ON c.id = t.category_id WHERE {SPEND} AND t.check_state = 'question'").fetchone()[0]


def mark(conn: sqlite3.Connection, ids: list[int], state: str | None, who: str, reason: str = "", note: str = "") -> int:
    """state: 'ok', 'question', or None to take the check away. Returns how many lines changed."""
    if state not in ("ok", "question", None):
        raise ValueError("unknown check")
    if state == "question" and reason not in REASONS:
        raise ValueError("Say why you are questioning it.")
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    if state is None:
        n = conn.execute(f"UPDATE transactions SET check_state = NULL, check_by = NULL, check_at = NULL, check_reason = NULL, check_note = NULL WHERE id IN ({marks})", ids).rowcount
    else:
        n = conn.execute(
            f"""UPDATE transactions SET check_state = ?, check_by = ?, check_at = ?, check_reason = ?, check_note = ?
                WHERE id IN ({marks})""",
            [state, who, now(), reason if state == "question" else None, (note or "").strip()[:300] or None] + ids).rowcount
    audit(conn, "check_expenses", "transactions", ids[0] if len(ids) == 1 else None,
          f"{n} line(s): {state or 'unchecked'}" + (f" ({reason})" if state == "question" else ""), who)
    conn.commit()
    return n
