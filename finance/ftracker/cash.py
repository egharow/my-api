"""Payments you add by hand, such as cash. They live in an account called Cash and count like any other payment."""
import sqlite3
import uuid
from datetime import date

from .db import audit, now
from .normalize import norm_description

ISSUER = "cash"


def _account(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE issuer = ? AND label = 'Cash'", (ISSUER,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO accounts (kind, issuer, label, currency, created_at) VALUES ('other', ?, 'Cash', 'ILS', ?)", (ISSUER, now()))
    return cur.lastrowid


def _batch(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM batches WHERE note = 'payments added by hand'").fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO batches (created_at, status, committed_at, note) VALUES (?, 'committed', ?, 'payments added by hand')", (now(), now()))
    return cur.lastrowid


def add(conn: sqlite3.Connection, when: str, amount: float, description: str, category: str, tag_ids: list[int] | None = None) -> int:
    date.fromisoformat(when)
    if amount <= 0:
        raise ValueError("Type the amount as a number above zero.")
    if not description.strip():
        raise ValueError("Say what it was for.")
    cat = conn.execute("SELECT id FROM categories WHERE name = ?", (category,)).fetchone()
    if not cat:
        raise ValueError("Choose a category.")
    cur = conn.execute(
        """INSERT INTO transactions (batch_id, account_id, dedupe_key, txn_date, budget_month, description, description_norm, amount, currency,
                                     kind, category_id, category_status, confidence, proposal_basis, created_at)
           VALUES (?,?,?,?,?,?,?,?,'ILS','purchase',?,'approved',1.0,'added by hand',?)""",
        (_batch(conn), _account(conn), f"manual|{uuid.uuid4()}", when, when[:7], description.strip(), norm_description(description), -round(amount, 2), cat["id"], now()))
    for t in tag_ids or []:
        conn.execute("INSERT OR IGNORE INTO transaction_tags (tag_id, transaction_id, included) VALUES (?, ?, 1)", (t, cur.lastrowid))
    audit(conn, "cash_added", "transaction", cur.lastrowid, f"{description} {amount}")
    conn.commit()
    return cur.lastrowid


def recent(conn: sqlite3.Connection, limit: int = 30):
    return conn.execute(
        """SELECT t.id, t.txn_date, t.description, -t.amount AS spent, c.name AS category FROM transactions t JOIN accounts a ON a.id = t.account_id
           JOIN categories c ON c.id = t.category_id WHERE a.issuer = ? ORDER BY t.txn_date DESC, t.id DESC LIMIT ?""", (ISSUER, limit)).fetchall()


def delete(conn: sqlite3.Connection, line_id: int) -> bool:
    n = conn.execute("DELETE FROM transactions WHERE id = ? AND account_id IN (SELECT id FROM accounts WHERE issuer = ?)", (line_id, ISSUER)).rowcount
    conn.commit()
    return bool(n)
