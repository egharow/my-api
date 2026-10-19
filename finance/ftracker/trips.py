"""Trips and tags. A trip has dates and collects what was spent in them; a tag only collects the payments you add."""
import sqlite3
from datetime import date

from .db import audit, now
from . import recurring

SPEND = ("c.neutral = 0 AND c.kind IN ('expense','fee','debt') AND t.kind != 'card_payment' AND t.currency = 'ILS' "
         "AND t.left_out = 0 AND t.amount < 0")


def create(conn: sqlite3.Connection, name: str, kind: str, start: str | None = None, end: str | None = None) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Give it a name.")
    if kind == "trip":
        if not (start and end):
            raise ValueError("A trip needs a first and a last day.")
        if date.fromisoformat(start) > date.fromisoformat(end):
            start, end = end, start
    else:
        start = end = None
    if conn.execute("SELECT 1 FROM tags WHERE name = ?", (name,)).fetchone():
        raise ValueError(f"There is already a trip or tag called {name}.")
    cur = conn.execute("INSERT INTO tags (name, kind, start_date, end_date, created_at) VALUES (?,?,?,?,?)", (name, kind, start, end, now()))
    audit(conn, "tag_created", "tag", cur.lastrowid, name)
    conn.commit()
    return cur.lastrowid


def delete(conn: sqlite3.Connection, tag_id: int) -> None:
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    conn.commit()


def get(conn: sqlite3.Connection, tag_id: int):
    return conn.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)).fetchone()


def all_tags(conn: sqlite3.Connection):
    return conn.execute("SELECT * FROM tags ORDER BY COALESCE(start_date, created_at) DESC").fetchall()


def attach(conn: sqlite3.Connection, tag_id: int, line_ids: list[int]) -> int:
    for i in line_ids:
        conn.execute("INSERT OR REPLACE INTO transaction_tags (tag_id, transaction_id, included) VALUES (?, ?, 1)", (tag_id, i))
    conn.commit()
    return len(line_ids)


def detach(conn: sqlite3.Connection, tag_id: int, line_id: int) -> None:
    """Take a payment out. Inside a trip's dates it is remembered as taken out, so it does not come back."""
    tag = get(conn, tag_id)
    if tag and tag["start_date"]:
        conn.execute("INSERT OR REPLACE INTO transaction_tags (tag_id, transaction_id, included) VALUES (?, ?, 0)", (tag_id, line_id))
    else:
        conn.execute("DELETE FROM transaction_tags WHERE tag_id = ? AND transaction_id = ?", (tag_id, line_id))
    conn.commit()


def lines(conn: sqlite3.Connection, tag_id: int) -> list[dict]:
    tag = get(conn, tag_id)
    if not tag:
        return []
    fixed = recurring.fixed_merchants(conn) if tag["start_date"] else set()
    out: dict[int, dict] = {}
    base = f"""SELECT t.id, t.txn_date, t.description, t.description_norm, -t.amount AS spent, t.orig_amount, t.orig_currency, c.name AS category,
                      a.label AS account, a.issuer AS issuer, c.kind AS ckind
               FROM transactions t JOIN categories c ON c.id = t.category_id JOIN accounts a ON a.id = t.account_id"""
    if tag["start_date"]:
        for r in conn.execute(f"{base} WHERE {SPEND} AND t.txn_date BETWEEN ? AND ?", (tag["start_date"], tag["end_date"])):
            if r["ckind"] == "debt" or r["description_norm"] in fixed:                      # the mortgage, loans and regular bills are not part of a trip
                continue
            out[r["id"]] = {**dict(r), "added": False}
    for r in conn.execute(f"{base} JOIN transaction_tags g ON g.transaction_id = t.id WHERE g.tag_id = ? AND g.included = 1 AND t.amount < 0", (tag_id,)):
        out[r["id"]] = {**dict(r), "added": True}
    for r in conn.execute("SELECT transaction_id FROM transaction_tags WHERE tag_id = ? AND included = 0", (tag_id,)):
        out.pop(r[0], None)
    return sorted(out.values(), key=lambda r: (r["txn_date"], r["id"]), reverse=True)


def summary(conn: sqlite3.Connection, tag_id: int) -> dict:
    rows = lines(conn, tag_id)
    tag = get(conn, tag_id)
    by_cat: dict[str, float] = {}
    for r in rows:
        by_cat[r["category"]] = round(by_cat.get(r["category"], 0) + r["spent"], 2)
    total = round(sum(r["spent"] for r in rows), 2)
    days = 0
    if tag and tag["start_date"]:
        days = (date.fromisoformat(tag["end_date"]) - date.fromisoformat(tag["start_date"])).days + 1
    foreign = round(sum(r["spent"] for r in rows if r["orig_currency"] not in (None, "ILS")), 2)
    return {"tag": tag, "lines": rows, "total": total, "by_category": sorted(by_cat.items(), key=lambda kv: -kv[1]),
            "days": days, "per_day": round(total / days, 2) if days else None, "foreign": foreign}


def search_lines(conn: sqlite3.Connection, text: str, limit: int = 40):
    like = f"%{text.strip()}%"
    return conn.execute(
        f"""SELECT t.id, t.txn_date, t.description, -t.amount AS spent, c.name AS category FROM transactions t JOIN categories c ON c.id = t.category_id
            WHERE t.amount < 0 AND t.kind != 'card_payment' AND (t.description LIKE ? OR t.detail LIKE ?) ORDER BY t.txn_date DESC LIMIT ?""",
        (like, like, limit)).fetchall()


def tags_of(conn: sqlite3.Connection, line_id: int) -> list[str]:
    return [r[0] for r in conn.execute("SELECT g.name FROM tags g JOIN transaction_tags x ON x.tag_id = g.id WHERE x.transaction_id = ? AND x.included = 1", (line_id,))]
