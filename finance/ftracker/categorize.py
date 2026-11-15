"""Propose a category for each new transaction. Nothing is final until you approve it.

Order, first match wins: your explicit rules and past approvals, learned rules, then the
built-in dictionary. Otherwise a similar past transaction is suggested, or it goes to review.
"""
import re
import sqlite3
import functools
from collections import defaultdict
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


def find_rule(conn: sqlite3.Connection, desc_norm: str, amount: float, account_kind: str, rules=None):
    if rules is None:
        rules = conn.execute("SELECT * FROM rules WHERE enabled = 1").fetchall()
    hits = [r for r in rules if _rule_matches(r, desc_norm, amount, account_kind)]
    if not hits:
        return None
    return max(hits, key=lambda r: (_source_rank(r["source"]), r["priority"], r["id"]))


class _Memory:
    """What you have already approved, read once and shaped for fast lookups, so a batch of suggestions does not
    re-read and re-compare the whole history for every single line."""

    def __init__(self, conn: sqlite3.Connection):
        self.rules = conn.execute("SELECT * FROM rules WHERE enabled = 1").fetchall()
        self.similar = [(merchant_key(r["description"]), r["category_id"], r["n"]) for r in conn.execute(
            """SELECT description, category_id, COUNT(*) AS n FROM transactions
               WHERE category_status = 'approved' AND category_id IS NOT NULL GROUP BY description, category_id""")]
        self.guess: dict[bool, list] = {True: [], False: []}
        self.index: dict[bool, dict] = {True: defaultdict(list), False: defaultdict(list)}
        for r in conn.execute(
                """SELECT t.description, t.category_id, (t.amount < 0) AS neg FROM transactions t JOIN categories c ON c.id = t.category_id
                   WHERE t.category_status = 'approved' AND c.name != 'Uncategorised' AND t.kind IN ('purchase','other','refund')"""):
            neg = bool(r["neg"])
            toks = _tokens(r["description"])
            i = len(self.guess[neg])
            self.guess[neg].append((r["description"], r["category_id"], toks))
            for tok in toks:
                self.index[neg][tok].append(i)


def _similar_past(conn: sqlite3.Connection, desc: str, mem: "_Memory | None" = None):
    key = merchant_key(desc)
    if mem is not None:
        rows = mem.similar
    else:
        rows = [(merchant_key(r["description"]), r["category_id"], r["n"]) for r in conn.execute(
            """SELECT description, category_id, COUNT(*) AS n FROM transactions
               WHERE category_status = 'approved' AND category_id IS NOT NULL
               GROUP BY description, category_id""")]
    best = None
    sm = SequenceMatcher(None)
    sm.set_seq1(key)
    la = len(key)
    for other, category_id, n in rows:
        lb = len(other)
        if la + lb == 0 or 2.0 * min(la, lb) / (la + lb) < SIMILARITY:             # lengths too different to be similar: skip at once
            continue
        sm.set_seq2(other)
        if sm.quick_ratio() < SIMILARITY:                                           # cheap upper bound before the real comparison
            continue
        score = sm.ratio()
        if score >= SIMILARITY and (best is None or (score, n) > (best[0], best[1])):
            best = (score, n, category_id)
    return best


SECTORS = {"מזון ומשקאות": "Groceries", "מזון מהיר": "Eating out", "מסעדות": "Eating out", "רפואה ובריאות": "Medical", "ריהוט ובית": "Home",
           "עמותות ותרומות": "Donations", "ביגוד והנעלה": "Clothes", "אופנה": "Clothes", "דלק": "Fuel & parking", "ביטוח": "Insurance",
           "חינוך": "Education", "קוסמטיקה": "Beauty care", "בילוי ופנאי": "Fun", "פנאי ובילוי": "Fun"}     # the card company's own sector names
_ONLINE = ("AMAZON", "MAZON", "MKTPL", "ALIEXPRESS", "LIEXPRESS", "TEMU", "SHEIN", "EBAY", "ETSY", "PAYPAL")   # foreign-currency shops that are not travel
HABIT_MIN = 3                      # approved this many times, always into the same category


def _habit(conn: sqlite3.Connection, desc_norm: str, amount: float):
    """(category_id, n) when you have approved this exact merchant at least HABIT_MIN times, always the same way."""
    if abs(amount) >= LARGE_ITEM:
        return None
    rows = conn.execute(
        """SELECT category_id, COUNT(*) AS n FROM transactions
           WHERE description_norm = ? AND category_status = 'approved' AND proposal_basis = 'approved by you'
             AND ((amount < 0) = (? < 0)) GROUP BY category_id""", (desc_norm, amount)).fetchall()
    if len(rows) == 1 and rows[0]["n"] >= HABIT_MIN:
        return rows[0]["category_id"], rows[0]["n"]
    return None


@functools.lru_cache(maxsize=100000)
def _tokens(desc: str) -> frozenset:
    """Words of a merchant name, plus 3-letter pieces so spelling variants still share evidence."""
    key = merchant_key(desc)
    words = {w for w in key.split() if len(w) >= 2}
    grams = {w[i:i + 3] for w in words for i in range(len(w) - 2)} if words else set()
    return frozenset(words | grams)


GUESS_MIN_SHARE = 0.55   # the winning category must hold at least this share of the evidence
GUESS_MIN_LINES = 3      # and that evidence must come from at least this many past payments


def _guess(conn: sqlite3.Connection, desc: str, amount: float, mem: "_Memory | None" = None):
    """Learn from every payment you already categorised: which category do this name's words point to?

    Returns (category_id, share, n_lines, example) or None. Only ever a suggestion for you to confirm.
    """
    toks = _tokens(desc)
    if not toks:
        return None
    neg = amount < 0
    if mem is None:
        mem = _Memory(conn)
    rows = mem.guess[neg]
    near: set[int] = set()
    for tok in toks:                                        # only past payments that share a word or piece can score at all
        near.update(mem.index[neg].get(tok, ()))
    score: dict[int, float] = {}
    support: dict[int, int] = {}
    example: dict[int, tuple[float, str]] = {}
    for i in near:
        description, cid, other = rows[i]
        shared = toks & other
        if not shared:
            continue
        weight = len(shared) / len(toks | other)             # Jaccard overlap
        if weight < 0.2:
            continue
        score[cid] = score.get(cid, 0.0) + weight
        support[cid] = support.get(cid, 0) + 1
        if weight > example.get(cid, (0, ""))[0]:
            example[cid] = (weight, description)
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


def classify(conn: sqlite3.Connection, txn_id: int, mem: "_Memory | None" = None) -> None:
    t = conn.execute(
        """SELECT t.*, a.kind AS account_kind FROM transactions t
           JOIN accounts a ON a.id = t.account_id WHERE t.id = ?""", (txn_id,)).fetchone()
    rule = find_rule(conn, t["description_norm"], t["amount"], t["account_kind"], mem.rules if mem else None)
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
        habit = _habit(conn, t["description_norm"], t["amount"])
        similar = None if habit else _similar_past(conn, t["description"], mem)
        if habit:
            category_id, n = habit
            status, confidence = "approved", 0.97
            basis = f"approved by itself: you put this merchant in the same category {n} times"
        elif similar:
            score, n, category_id = similar
            confidence = round(score * 0.9, 2)
            basis = f"similar to {n} earlier approved payment(s)"
        else:
            guess = _guess(conn, t["description"], t["amount"], mem)
            if guess:
                category_id, share, n, example = guess
                confidence = round(min(share, 0.85) * 0.6, 2)
                basis = f"best guess from {n} payments with similar words, e.g. “{example}”"
    if (category_id == uncategorised_id(conn) and not rule and t["orig_currency"] not in (None, "ILS")
            and not any(w in t["description_norm"] for w in _ONLINE)):
        travel = conn.execute("SELECT id FROM categories WHERE name = 'Travel'").fetchone()
        if travel:
            category_id, confidence = travel["id"], 0.3
            basis = f"paid in {t['orig_currency']}: a trip or online shopping, please check"
    if category_id == uncategorised_id(conn) and not rule and (t["detail"] or "").startswith("ענף:"):
        sector = (t["detail"].split("·")[0])[len("ענף:"):].strip()
        mapped = conn.execute("SELECT id FROM categories WHERE name = ?", (SECTORS.get(sector, ""),)).fetchone()
        if mapped:
            category_id, confidence, basis = mapped["id"], 0.5, f"the card company calls it “{sector}”"
    if t["amount"] > 0 and kind == "purchase":
        kind = "refund"
    ck = conn.execute("SELECT kind FROM categories WHERE id = ?", (category_id,)).fetchone()
    if ck and ck["kind"] in ("transfer", "saving") and kind in ("purchase", "refund", "other"):
        kind = "transfer"                  # a line in a transfer or savings category is a move of money, so it is paired and tracked as one

    conn.execute(
        """UPDATE transactions SET category_id = ?, category_status = ?, confidence = ?,
                  proposal_basis = ?, kind = ?, expects_refund_by = ?, counterparty_account_id = ?
           WHERE id = ?""",
        (category_id, status, confidence, basis, kind, refund_by, counterparty, txn_id))


def memory(conn: sqlite3.Connection) -> "_Memory":
    """Read what you have approved once, to give to classify() when many lines are suggested together."""
    return _Memory(conn)


def resuggest(conn: sqlite3.Connection, near: list[str] | None = None) -> int:
    """Run the suggestions again for lines still waiting for you, now that more of your choices are known.

    Only lines nobody has approved or edited and that sit in a draft import are touched. With `near` (the merchants you
    just approved), only waiting lines that share a word with them are looked at, since no other line can change.
    """
    rows = conn.execute(
        """SELECT t.id, t.description FROM transactions t JOIN batches b ON b.id = t.batch_id
           WHERE t.category_status = 'proposed' AND b.status != 'committed'
             AND (t.category_id = (SELECT id FROM categories WHERE name = 'Uncategorised')
                  OR t.proposal_basis LIKE 'best guess%' OR t.proposal_basis LIKE 'similar to%')""").fetchall()
    if near is not None:
        wanted: set = set()
        for d in near:
            wanted |= _tokens(d)
        rows = [r for r in rows if _tokens(r["description"]) & wanted]
    ids = [r["id"] for r in rows]
    unc = uncategorised_id(conn)
    before = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_status='proposed' AND category_id = ?", (unc,)).fetchone()[0]
    if ids:
        mem = _Memory(conn)
        for tid in ids:
            classify(conn, tid, mem)
    after = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_status='proposed' AND category_id = ?", (unc,)).fetchone()[0]
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
    cat = conn.execute("SELECT id, kind FROM categories WHERE name = ?", (category_name,)).fetchone()
    if not cat:
        raise ValueError(f"unknown category {category_name!r}")
    cat_kind = cat["kind"]
    _ensure_editable(conn, txn_ids)
    for tid in txn_ids:
        conn.execute(
            """UPDATE transactions SET category_id = ?, category_status = 'approved',
                      confidence = 1.0, proposal_basis = 'approved by you' WHERE id = ?""",
            (cat["id"], tid))
        audit(conn, "approve_category", "transaction", tid, category_name, actor)
        if cat_kind in ("expense", "fee", "debt"):             # a payment moved out of a transfer category is an ordinary payment
            conn.execute("UPDATE transactions SET kind = 'purchase' WHERE id = ? AND kind = 'transfer'", (tid,))
        elif cat_kind in ("transfer", "saving"):               # and one moved into a transfer or savings category is a move of money
            conn.execute("UPDATE transactions SET kind = 'transfer' WHERE id = ? AND kind IN ('purchase', 'refund', 'other')", (tid,))
    if learn and txn_ids:
        desc = conn.execute("SELECT description_norm FROM transactions WHERE id = ?",
                            (txn_ids[0],)).fetchone()[0]
        key = merchant_key(desc)
        add_learned_rule(conn, key if len(key) >= 4 else desc, category_name)
    from . import reconcile
    reconcile.resolve_stale(conn)
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
    mem = _Memory(conn) if ids else None
    for tid in ids:
        classify(conn, tid, mem)
    conn.commit()
    return len(ids)


def _ensure_editable(conn: sqlite3.Connection, txn_ids: list[int]) -> None:
    if not txn_ids:
        return
    marks = ",".join("?" * len(txn_ids))
    row = conn.execute(
        f"""SELECT COUNT(*) FROM transactions t JOIN batches b ON b.id = t.batch_id JOIN accounts a ON a.id = t.account_id
            WHERE t.id IN ({marks}) AND b.status = 'committed' AND a.issuer != 'cash'""", txn_ids).fetchone()
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
        """SELECT c.id, c.name, c.kind, c.neutral, COUNT(t.id) AS lines FROM categories c
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
    sync_kinds(conn, b["id"])
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


TREATMENTS = {
    "spending": "Counts as spending",
    "left_out": "Left out of the totals",
    "saving": "Savings and investing (left out, counted as invested)",
    "income": "Money coming in",
}


def treatment_of(kind: str, neutral: int) -> str:
    if kind == "saving":
        return "saving"
    if kind == "income":
        return "income"
    return "left_out" if neutral else "spending"


def set_treatment(conn: sqlite3.Connection, category_id: int, treatment: str) -> bool:
    """Decide for a whole category how it counts. Every line in it follows, past and future. Returns True when something changed."""
    row = conn.execute("SELECT name, kind, neutral FROM categories WHERE id = ?", (category_id,)).fetchone()
    if not row or row["name"] == "Uncategorised":
        return False
    if treatment not in TREATMENTS:
        raise ValueError("unknown choice")
    kind, neutral = row["kind"], row["neutral"]
    if treatment == "spending":
        kind, neutral = (kind if kind in ("expense", "fee", "debt") else "expense"), 0
    elif treatment == "left_out":
        kind, neutral = (kind if kind in ("expense", "fee", "debt", "transfer") else "transfer"), 1
    elif treatment == "saving":
        kind, neutral = "saving", 1
    else:
        kind, neutral = "income", 0
    if (kind, neutral) == (row["kind"], row["neutral"]):
        return False
    conn.execute("UPDATE categories SET kind = ?, neutral = ? WHERE id = ?", (kind, neutral, category_id))
    sync_kinds(conn, category_id)
    return True


def sync_kinds(conn: sqlite3.Connection, category_id: int) -> int:
    """Make every line's kind agree with its category: lines in a transfer or savings category are transfers, and lines in a
    spending category that were transfers are ordinary payments. Used after a category is merged or its meaning is changed."""
    row = conn.execute("SELECT kind FROM categories WHERE id = ?", (category_id,)).fetchone()
    if not row:
        return 0
    if row["kind"] in ("transfer", "saving"):
        n = conn.execute("UPDATE transactions SET kind = 'transfer' WHERE category_id = ? AND kind IN ('purchase', 'refund', 'other')", (category_id,)).rowcount
    elif row["kind"] in ("expense", "fee", "debt"):
        n = conn.execute("UPDATE transactions SET kind = CASE WHEN amount > 0 THEN 'refund' ELSE 'purchase' END WHERE category_id = ? AND kind = 'transfer'", (category_id,)).rowcount
    else:
        n = 0
    return n


def repair_transfer_kinds(conn: sqlite3.Connection) -> int:
    """Lines you filed under a transfer or savings category earlier, by hand, become transfers now, and are paired with the other side."""
    n = 0
    for r in conn.execute("SELECT id FROM categories WHERE kind IN ('transfer', 'saving')").fetchall():
        n += sync_kinds(conn, r["id"])
    if n:
        from . import reconcile
        reconcile.pair_transfers(conn)
        reconcile.resolve_stale(conn)
    conn.commit()
    return n


def add_category(conn: sqlite3.Connection, name: str, treatment: str = "spending") -> None:
    """A category of your own, for example Water or Arnona. `treatment` is how it counts (see TREATMENTS)."""
    name = name.strip()
    if not name or len(name) > 60:
        raise ValueError("Type a name for the category.")
    if conn.execute("SELECT 1 FROM categories WHERE lower(name) = lower(?)", (name,)).fetchone():
        raise ValueError(f"There is already a category called “{name}”.")
    if treatment not in TREATMENTS:
        raise ValueError("unknown choice")
    kind, neutral = {"spending": ("expense", 0), "left_out": ("transfer", 1), "saving": ("saving", 1), "income": ("income", 0)}[treatment]
    conn.execute("INSERT INTO categories (name, parent_id, kind, neutral) VALUES (?, NULL, ?, ?)", (name, kind, neutral))
    audit(conn, "category_added", "category", None, name)
