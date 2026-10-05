"""Propose a category for each new transaction. Nothing is final until you approve it.

Order, first match wins: your explicit rules and past approvals, learned rules, then the
built-in dictionary. Otherwise a similar past transaction is suggested, or it goes to review.
"""
import re
import sqlite3
from difflib import SequenceMatcher

from .db import audit, now
from .normalize import merchant_key, norm_description

LARGE_ITEM = 1000.0  # one-off amounts at or above this always go to review
SIMILARITY = 0.88


def _rule_matches(rule: sqlite3.Row, desc_norm: str, amount: float, account_kind: str) -> bool:
    if rule["account_kind"] and rule["account_kind"] != account_kind:
        return False
    if rule["direction"] == "in" and amount <= 0:
        return False
    if rule["direction"] == "out" and amount >= 0:
        return False
    magnitude = abs(amount)
    if rule["min_amount"] is not None and magnitude < rule["min_amount"]:
        return False
    if rule["max_amount"] is not None and magnitude > rule["max_amount"]:
        return False
    pattern = rule["pattern"]
    if rule["is_regex"]:
        return re.search(pattern, desc_norm, re.IGNORECASE) is not None
    needle = norm_description(pattern)
    if needle.isascii() and len(needle) <= 4:   # short Latin tokens such as BIT: whole word only
        return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", desc_norm) is not None
    return needle in desc_norm


def _source_rank(source: str) -> int:
    return {"user": 3, "learned": 2, "builtin": 1}[source]


def find_rule(conn: sqlite3.Connection, desc_norm: str, amount: float, account_kind: str):
    rules = conn.execute("SELECT * FROM rules WHERE enabled = 1").fetchall()
    hits = [r for r in rules if _rule_matches(r, desc_norm, amount, account_kind)]
    if not hits:
        return None
    return max(hits, key=lambda r: (_source_rank(r["source"]), r["priority"], r["id"]))


def _similar_past(conn: sqlite3.Connection, desc: str):
    key = merchant_key(desc)
    rows = conn.execute(
        """SELECT description, category_id, COUNT(*) AS n FROM transactions
           WHERE category_status = 'approved' AND category_id IS NOT NULL
           GROUP BY description, category_id""").fetchall()
    best = None
    for r in rows:
        score = SequenceMatcher(None, key, merchant_key(r["description"])).ratio()
        if score >= SIMILARITY and (best is None or (score, r["n"]) > (best[0], best[1])):
            best = (score, r["n"], r["category_id"])
    return best


def uncategorised_id(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT id FROM categories WHERE name = 'Uncategorised'").fetchone()[0]


def classify(conn: sqlite3.Connection, txn_id: int) -> None:
    t = conn.execute(
        """SELECT t.*, a.kind AS account_kind FROM transactions t
           JOIN accounts a ON a.id = t.account_id WHERE t.id = ?""", (txn_id,)).fetchone()
    rule = find_rule(conn, t["description_norm"], t["amount"], t["account_kind"])
    kind = t["kind"]
    category_id = uncategorised_id(conn)
    status, confidence, basis, refund_by = "proposed", 0.0, "no match: needs your review", None
    counterparty = t["counterparty_account_id"]

    if rule:
        category_id = rule["category_id"]
        confidence = {"user": 1.0, "learned": 0.95, "builtin": 0.8}[rule["source"]]
        basis = f"{rule['source']} rule: “{rule['pattern']}”"
        if rule["auto_approve"] or rule["source"] in ("user", "learned"):
            status = "approved"
        if rule["set_kind"]:
            kind = rule["set_kind"]
        if rule["counterparty_account_id"]:
            counterparty = rule["counterparty_account_id"]
        if rule["expects_refund_days"] and t["amount"] < 0:
            from datetime import date, timedelta
            refund_by = (date.fromisoformat(t["txn_date"])
                         + timedelta(days=rule["expects_refund_days"])).isoformat()
    else:
        similar = _similar_past(conn, t["description"])
        if similar:
            score, n, category_id = similar
            confidence = round(score * 0.9, 2)
            basis = f"similar to {n} earlier approved payment(s)"
    if t["amount"] > 0 and kind == "purchase":
        kind = "refund"

    conn.execute(
        """UPDATE transactions SET category_id = ?, category_status = ?, confidence = ?,
                  proposal_basis = ?, kind = ?, expects_refund_by = ?, counterparty_account_id = ?
           WHERE id = ?""",
        (category_id, status, confidence, basis, kind, refund_by, counterparty, txn_id))


def large_unreviewed(conn: sqlite3.Connection, batch_id: int | None = None):
    sql = """SELECT * FROM transactions WHERE category_status = 'proposed'
             AND ABS(amount) >= ? AND kind IN ('purchase','other')"""
    args: list = [LARGE_ITEM]
    if batch_id is not None:
        sql += " AND batch_id = ?"
        args.append(batch_id)
    return conn.execute(sql, args).fetchall()


def approve(conn: sqlite3.Connection, txn_ids: list[int], category_name: str,
            learn: bool = False, actor: str = "user") -> int:
    """Set a category on transactions. With learn=True, also save a rule for the merchant."""
    cat = conn.execute("SELECT id FROM categories WHERE name = ?", (category_name,)).fetchone()
    if not cat:
        raise ValueError(f"unknown category {category_name!r}")
    _ensure_editable(conn, txn_ids)
    for tid in txn_ids:
        conn.execute(
            """UPDATE transactions SET category_id = ?, category_status = 'approved',
                      confidence = 1.0, proposal_basis = 'approved by you' WHERE id = ?""",
            (cat["id"], tid))
        audit(conn, "approve_category", "transaction", tid, category_name, actor)
    if learn and txn_ids:
        desc = conn.execute("SELECT description_norm FROM transactions WHERE id = ?",
                            (txn_ids[0],)).fetchone()[0]
        add_learned_rule(conn, desc, category_name)
    conn.commit()
    return len(txn_ids)


def add_learned_rule(conn: sqlite3.Connection, pattern: str, category_name: str,
                     source: str = "learned") -> int:
    cat = conn.execute("SELECT id FROM categories WHERE name = ?", (category_name,)).fetchone()
    pattern = norm_description(pattern)
    existing = conn.execute(
        "SELECT id FROM rules WHERE pattern = ? AND source = ?", (pattern, source)).fetchone()
    if existing:
        conn.execute("UPDATE rules SET category_id = ?, enabled = 1 WHERE id = ?",
                     (cat["id"], existing["id"]))
        return existing["id"]
    cur = conn.execute(
        """INSERT INTO rules (source, pattern, category_id, auto_approve, priority, created_at)
           VALUES (?, ?, ?, 1, ?, ?)""", (source, pattern, cat["id"], len(pattern), now()))
    return cur.lastrowid


def preview_rule(conn: sqlite3.Connection, pattern: str) -> list[sqlite3.Row]:
    """Transactions a new rule would touch, shown to you before it is saved."""
    needle = norm_description(pattern)
    return conn.execute(
        "SELECT id, txn_date, description, amount, category_id FROM transactions "
        "WHERE description_norm LIKE ? ORDER BY txn_date", (f"%{needle}%",)).fetchall()


def apply_rules_to_proposed(conn: sqlite3.Connection) -> int:
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM transactions WHERE category_status = 'proposed'")]
    for tid in ids:
        classify(conn, tid)
    conn.commit()
    return len(ids)


def _ensure_editable(conn: sqlite3.Connection, txn_ids: list[int]) -> None:
    if not txn_ids:
        return
    marks = ",".join("?" * len(txn_ids))
    row = conn.execute(
        f"""SELECT COUNT(*) FROM transactions t JOIN batches b ON b.id = t.batch_id
            WHERE t.id IN ({marks}) AND b.status = 'committed'""", txn_ids).fetchone()
    if row[0]:
        raise PermissionError("these transactions belong to a committed import; reopen it first")


def add_user_rule(conn: sqlite3.Connection, pattern: str, category_name: str, set_kind: str | None = None,
                  direction: str | None = None, note: str | None = None) -> dict:
    """An explicit rule from you. Outranks learned and built-in rules; re-applied to unapproved items."""
    cat = conn.execute("SELECT id FROM categories WHERE name = ?", (category_name,)).fetchone()
    if not cat:
        raise ValueError(f"unknown category {category_name!r}")
    pat = norm_description(pattern)
    conn.execute(
        """INSERT INTO rules (source, pattern, direction, category_id, set_kind, auto_approve, priority, note, created_at)
           VALUES ('user', ?, ?, ?, ?, 1, ?, ?, ?)""",
        (pat, direction, cat["id"], set_kind, 1000 + len(pat), note, now()))
    audit(conn, "rule_added", "rule", None, f"{pat} -> {category_name}")
    touched = apply_rules_to_proposed(conn)
    return {"pattern": pat, "unapproved_reviewed": touched}
