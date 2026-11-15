"""Splitting one payment into two categories.

A municipal bill, for example, is property tax plus water on one payment. A split keeps the bank or card line as it
is in your records, but counts it in two parts: the original line becomes the first part, and a new line, tied to it,
holds the rest. The parts always add up to the original amount, so statement totals still check out.
A split rule does this by itself: a merchant, a fixed amount for the first part, and where the rest goes.
"""
import sqlite3

from .db import audit, now
from .normalize import norm_description

TOLERANCE = 0.01


def _cat(conn: sqlite3.Connection, name: str):
    row = conn.execute("SELECT id, kind FROM categories WHERE name = ?", (name,)).fetchone()
    if not row:
        raise ValueError(f"There is no category called “{name}”.")
    return row


def is_part(conn: sqlite3.Connection, tid: int) -> bool:
    r = conn.execute("SELECT split_of, split_total FROM transactions WHERE id = ?", (tid,)).fetchone()
    return bool(r and (r["split_of"] or r["split_total"] is not None))


def split_line(conn: sqlite3.Connection, tid: int, first_amount: float, first_category: str, rest_category: str,
               actor: str = "user", basis: str = "split by you") -> int | None:
    """Make the line two parts. first_amount is the size of the first part (positive). Returns the new part's id."""
    t = conn.execute("SELECT * FROM transactions WHERE id = ?", (tid,)).fetchone()
    if not t:
        raise ValueError("That payment was not found.")
    if is_part(conn, tid):
        raise ValueError("This payment is already split. Undo the split first.")
    total = abs(t["amount"])
    first = round(float(first_amount), 2)
    if first <= 0 or first >= total - TOLERANCE:
        raise ValueError(f"The first part must be more than zero and less than the payment (₪{total:,.2f}).")
    c1, c2 = _cat(conn, first_category), _cat(conn, rest_category)
    sign = -1 if t["amount"] < 0 else 1
    rest = round(total - first, 2)
    conn.execute("UPDATE transactions SET amount = ?, split_total = ?, category_id = ?, category_status = 'approved', confidence = 1.0, "
                 "proposal_basis = ? WHERE id = ?", (sign * first, t["amount"], c1["id"], basis, tid))
    cur = conn.execute(
        """INSERT INTO transactions (batch_id, account_id, statement_id, dedupe_key, txn_date, billing_date, budget_month, description,
               description_norm, detail, amount, currency, kind, is_recurring, category_id, category_status, confidence, proposal_basis,
               created_at, split_of)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'approved',1.0,?,?,?)""",
        (t["batch_id"], t["account_id"], t["statement_id"], t["dedupe_key"] + "|part2", t["txn_date"], t["billing_date"], t["budget_month"],
         t["description"] + " · rest", norm_description(t["description"] + " rest"), t["detail"], sign * rest, t["currency"], t["kind"],
         t["is_recurring"], c2["id"], basis, now(), tid))
    audit(conn, "split_payment", "transaction", tid, f"{total:,.2f} -> {first:,.2f} {first_category} + {rest:,.2f} {rest_category}", actor)
    return cur.lastrowid


def unsplit(conn: sqlite3.Connection, tid: int, actor: str = "user") -> bool:
    """Put a split payment back as one line, waiting for a category again."""
    t = conn.execute("SELECT id, split_of, split_total FROM transactions WHERE id = ?", (tid,)).fetchone()
    if not t:
        return False
    parent = t["split_of"] or (tid if t["split_total"] is not None else None)
    if not parent:
        return False
    p = conn.execute("SELECT split_total FROM transactions WHERE id = ?", (parent,)).fetchone()
    conn.execute("DELETE FROM transactions WHERE split_of = ?", (parent,))
    unc = conn.execute("SELECT id FROM categories WHERE name = 'Uncategorised'").fetchone()[0]
    conn.execute("UPDATE transactions SET amount = ?, split_total = NULL, category_id = ?, category_status = 'proposed', confidence = 0, "
                 "proposal_basis = 'split undone: needs your review' WHERE id = ?", (p["split_total"], unc, parent))
    audit(conn, "unsplit_payment", "transaction", parent, None, actor)
    return True


# ---- rules -------------------------------------------------------------------------------------------------

def rules(conn: sqlite3.Connection):
    return conn.execute(
        """SELECT r.id, r.pattern, r.fixed_amount, r.enabled, c1.name AS fixed_cat, c2.name AS rest_cat
           FROM split_rules r JOIN categories c1 ON c1.id = r.fixed_category_id JOIN categories c2 ON c2.id = r.rest_category_id
           ORDER BY r.id""").fetchall()


def add_rule(conn: sqlite3.Connection, pattern: str, fixed_amount: float, fixed_category: str, rest_category: str) -> int:
    pattern = norm_description(pattern)
    if not pattern:
        raise ValueError("Type the words that appear on the payment, for example the municipality's name.")
    if fixed_amount <= 0:
        raise ValueError("The fixed amount must be more than zero.")
    c1, c2 = _cat(conn, fixed_category), _cat(conn, rest_category)
    if c1["id"] == c2["id"]:
        raise ValueError("Choose two different categories.")
    cur = conn.execute("INSERT INTO split_rules (pattern, fixed_amount, fixed_category_id, rest_category_id, created_at) VALUES (?,?,?,?,?)",
                       (pattern, round(fixed_amount, 2), c1["id"], c2["id"], now()))
    audit(conn, "split_rule_added", "split_rule", cur.lastrowid, f"{pattern}: {fixed_amount:,.2f} {fixed_category}, rest {rest_category}")
    return cur.lastrowid


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    conn.execute("DELETE FROM split_rules WHERE id = ?", (rule_id,))


def apply_to_line(conn: sqlite3.Connection, tid: int, active=None) -> str | None:
    """Apply the first matching rule to one new line. Returns what was done ('split', 'whole') or None."""
    t = conn.execute("SELECT id, description_norm, amount, left_out, split_of, split_total, kind FROM transactions WHERE id = ?", (tid,)).fetchone()
    if not t or t["amount"] >= 0 or t["left_out"] or t["split_of"] or t["split_total"] is not None or t["kind"] in ("card_payment", "transfer"):
        return None
    for r in (active if active is not None else rules(conn)):
        if not r["enabled"] or r["pattern"] not in t["description_norm"]:
            continue
        total = abs(t["amount"])
        if total < r["fixed_amount"] - TOLERANCE:
            continue                                                 # smaller than the fixed part: not this kind of bill
        if abs(total - r["fixed_amount"]) <= TOLERANCE:              # exactly the fixed part: the whole payment is that category
            cid = conn.execute("SELECT id FROM categories WHERE name = ?", (r["fixed_cat"],)).fetchone()[0]
            now_cat = conn.execute("SELECT category_id, proposal_basis FROM transactions WHERE id = ?", (tid,)).fetchone()
            if now_cat["category_id"] == cid and now_cat["proposal_basis"] == "split rule":
                return None                                              # already counted that way
            conn.execute("UPDATE transactions SET category_id = ?, category_status = 'approved', confidence = 1.0, proposal_basis = 'split rule' WHERE id = ?", (cid, tid))
            return "whole"
        split_line(conn, tid, r["fixed_amount"], r["fixed_cat"], r["rest_cat"], "rule", "split rule")
        return "split"
    return None


def apply_to_all(conn: sqlite3.Connection) -> dict:
    """Go through every payment already in your records (submitted imports too) and apply the rules."""
    active = rules(conn)
    out = {"split": 0, "whole": 0}
    if not active:
        return out
    for r in conn.execute("SELECT id FROM transactions WHERE amount < 0 AND split_of IS NULL AND split_total IS NULL AND left_out = 0").fetchall():
        res = apply_to_line(conn, r["id"], active)
        if res:
            out[res] += 1
    return out
