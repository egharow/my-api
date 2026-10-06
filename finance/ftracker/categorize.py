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


def _tokens(desc: str) -> set[str]:
    """Words of a merchant name, plus 3-letter pieces so spelling variants still share evidence."""
    key = merchant_key(desc)
    words = {w for w in key.split() if len(w) >= 2}
    grams = {w[i:i + 3] for w in words for i in range(len(w) - 2)} if words else set()
    return words | grams


GUESS_MIN_SHARE = 0.55   # the winning category must hold at least this share of the evidence
GUESS_MIN_LINES = 3      # and that evidence must come from at least this many past payments


def _guess(conn: sqlite3.Connection, desc: str, amount: float):
    """Learn from every payment you already categorised: which category do this name's words point to?

    Returns (category_id, share, n_lines, example) or None. Only ever a suggestion for you to confirm.
    """
    toks = _tokens(desc)
    if not toks:
        return None
    rows = conn.execute(
        """SELECT t.description, t.category_id FROM transactions t JOIN categories c ON c.id = t.category_id
           WHERE t.category_status = 'approved' AND c.name != 'Uncategorised' AND t.kind IN ('purchase','other','refund')
             AND ((t.amount < 0) = (? < 0))""", (amount,)).fetchall()
    score: dict[int, float] = {}
    support: dict[int, int] = {}
    example: dict[int, tuple[float, str]] = {}
    for r in rows:
        shared = toks & _tokens(r["description"])
        if not shared:
            continue
        weight = len(shared) / len(toks | _tokens(r["description"]))     # Jaccard overlap
        if weight < 0.2:
            continue
        cid = r["category_id"]
        score[cid] = score.get(cid, 0.0) + weight
        support[cid] = support.get(cid, 0) + 1
        if weight > example.get(cid, (0, ""))[0]:
            example[cid] = (weight, r["description"])
    total = sum(score.values())
    if not total:
        return None
    cid = max(score, key=score.get)
    share = score[cid] / total
    if share < GUESS_MIN_SHARE or support[cid] < GUESS_MIN_LINES:
        return None
    return cid, share, support[cid], example[cid][1]


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
        else:
            guess = _guess(conn, t["description"], t["amount"])
            if guess:
                category_id, share, n, example = guess
                confidence = round(min(share, 0.85) * 0.6, 2)
                basis = f"best guess from {n} payments with similar words, e.g. “{example}”"
    if t["amount"] > 0 and kind == "purchase":
        kind = "refund"

    conn.execute(
        """UPDATE transactions SET category_id = ?, category_status = ?, confidence = ?,
                  proposal_basis = ?, kind = ?, expects_refund_by = ?, counterparty_account_id = ?
           WHERE id = ?""",
        (category_id, status, confidence, basis, kind, refund_by, counterparty, txn_id))


def resuggest(conn: sqlite3.Connection) -> int:
    """Run the suggestions again for lines still waiting for you, now that more of your choices are known.

    Only lines nobody has approved or edited and that sit in a draft import are touched.
    """
    ids = [r[0] for r in conn.execute(
        """SELECT t.id FROM transactions t JOIN batches b ON b.id = t.batch_id
           WHERE t.category_status = 'proposed' AND b.status != 'committed'
             AND (t.category_id = (SELECT id FROM categories WHERE name = 'Uncategorised')
                  OR t.proposal_basis LIKE 'best guess%' OR t.proposal_basis LIKE 'similar to%')""")]
    before = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_status='proposed' AND category_id = ?",
                          (uncategorised_id(conn),)).fetchone()[0]
    for tid in ids:
        classify(conn, tid)
    after = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_status='proposed' AND category_id = ?",
                         (uncategorised_id(conn),)).fetchone()[0]
    return max(before - after, 0)


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
        key = merchant_key(desc)
        add_learned_rule(conn, key if len(key) >= 4 else desc, category_name)
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


def add_rule_from_form(conn: sqlite3.Connection, pattern: str, category: str, mode: str,
                       destination: str | None = None) -> None:
    """Rules as the Setup page offers them: ordinary, money coming in, or a transfer somewhere."""
    if not pattern.strip():
        raise ValueError("type the words that appear on the statement line")
    if mode == "income":
        add_user_rule(conn, pattern, category, "income", "in")
    elif mode == "transfer_household":
        add_user_rule(conn, pattern, "Transfer to household", "transfer")
    elif mode == "transfer_account":
        from .accounts import set_destination_rule
        if not destination:
            raise ValueError("choose the account the money goes to")
        set_destination_rule(conn, pattern, destination)
    else:
        add_user_rule(conn, pattern, category)
    conn.commit()


def toggle_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    conn.execute("UPDATE rules SET enabled = 1 - enabled WHERE id = ?", (rule_id,))
    conn.commit()


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    row = conn.execute("SELECT source FROM rules WHERE id = ?", (rule_id,)).fetchone()
    if not row:
        return
    if row["source"] == "builtin":
        raise PermissionError("built-in rules can be switched off but not deleted")
    conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    conn.commit()


def category_counts(conn: sqlite3.Connection):
    return conn.execute(
        """SELECT c.id, c.name, c.kind, COUNT(t.id) AS lines FROM categories c
           LEFT JOIN transactions t ON t.category_id = c.id GROUP BY c.id ORDER BY lines DESC, c.name""").fetchall()


def merge_category(conn: sqlite3.Connection, src: str, dst: str) -> dict:
    """Move every line and rule from one category into another. A category you created is removed afterwards."""
    from .seed import CATEGORIES
    if src == dst:
        raise ValueError("choose two different categories")
    if src == "Uncategorised":
        raise ValueError("Uncategorised is where unreviewed lines wait; it cannot be merged away")
    a = conn.execute("SELECT id FROM categories WHERE name = ?", (src,)).fetchone()
    b = conn.execute("SELECT id FROM categories WHERE name = ?", (dst,)).fetchone()
    if not a or not b:
        raise ValueError("unknown category")
    locked = conn.execute(
        """SELECT COUNT(*) FROM transactions t JOIN batches x ON x.id = t.batch_id
           WHERE t.category_id = ? AND x.status = 'committed'""", (a["id"],)).fetchone()[0]
    if locked:
        raise PermissionError(f"{locked} line(s) of “{src}” are in a submitted import; reopen it first")
    moved = conn.execute("UPDATE transactions SET category_id = ? WHERE category_id = ?", (b["id"], a["id"])).rowcount
    rules = conn.execute("UPDATE rules SET category_id = ? WHERE category_id = ?", (b["id"], a["id"])).rowcount
    removed = False
    if src not in {c[0] for c in CATEGORIES}:
        conn.execute("UPDATE categories SET parent_id = NULL WHERE parent_id = ?", (a["id"],))
        conn.execute("DELETE FROM categories WHERE id = ?", (a["id"],))
        removed = True
    audit(conn, "merge_category", "category", b["id"], f"{src} -> {dst}: {moved} lines, {rules} rules")
    conn.commit()
    return {"lines": moved, "rules": rules, "removed": removed}


def rename_category(conn: sqlite3.Connection, old: str, new: str) -> None:
    new = new.strip()
    if not new:
        raise ValueError("type the new name")
    if old == "Uncategorised":
        raise ValueError("Uncategorised cannot be renamed")
    if conn.execute("SELECT 1 FROM categories WHERE name = ?", (new,)).fetchone():
        raise ValueError(f"there is already a category called “{new}”; merge into it instead")
    if not conn.execute("UPDATE categories SET name = ? WHERE name = ?", (new, old)).rowcount:
        raise ValueError("unknown category")
    audit(conn, "rename_category", "category", None, f"{old} -> {new}")
    conn.commit()
