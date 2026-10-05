"""Cross-checks run after every import: card payments vs card statements, bank fee refunds,
balance chains, large items and transfers with no destination."""
import sqlite3
from collections import Counter
from datetime import date, timedelta

from . import discrepancies as dx
from .categorize import LARGE_ITEM
from .parsers.base import ParsedTxn

PAYMENT_WINDOW_DAYS = 3
REFUND_SEARCH_DAYS = 45


def chain_problems(txns: list[ParsedTxn]) -> list[date]:
    """Dates of rows that break the running balance.

    The date column is a value date and rows are not always processed in date order, so the
    check does not rely on order. Each row moves the balance from (balance - amount) to
    balance; the rows form one unbroken path exactly when every 'before' balance except the
    opening one is some row's 'after' balance, and vice versa except for the closing one.
    """
    rows = [t for t in txns if t.balance_after is not None]
    if not rows:
        return []
    after = Counter(round(t.balance_after, 2) for t in rows)
    before = Counter(round(t.balance_after - t.amount, 2) for t in rows)
    opening, closing = before - after, after - before
    if sum(opening.values()) <= 1 and sum(closing.values()) <= 1:
        return []
    oldest = min(t.txn_date for t in rows)
    newest = max(t.txn_date for t in rows)
    bad = set()
    for t in rows:
        if round(t.balance_after - t.amount, 2) in opening and t.txn_date != oldest:
            bad.add(t.txn_date)
        if round(t.balance_after, 2) in closing and t.txn_date != newest:
            bad.add(t.txn_date)
    return sorted(bad) or [oldest]


def _bank_coverage(conn):
    return conn.execute(
        """SELECT s.account_id, s.period_start, s.period_end FROM statements s
           JOIN accounts a ON a.id = s.account_id WHERE a.kind = 'bank'""").fetchall()


def _money(x: float) -> str:
    return f"₪{abs(x):,.2f}"


def match_card_payments(conn: sqlite3.Connection, batch_id: int) -> None:
    payments = conn.execute(
        """SELECT t.* FROM transactions t WHERE t.kind = 'card_payment' AND t.amount < 0
           AND NOT EXISTS (SELECT 1 FROM statements s WHERE s.matched_bank_txn_id = t.id)""").fetchall()
    for p in payments:
        lo = (date.fromisoformat(p["txn_date"]) - timedelta(days=PAYMENT_WINDOW_DAYS)).isoformat()
        hi = (date.fromisoformat(p["txn_date"]) + timedelta(days=PAYMENT_WINDOW_DAYS)).isoformat()
        stmt = conn.execute(
            """SELECT * FROM statements WHERE matched_bank_txn_id IS NULL AND billing_date BETWEEN ? AND ?
               AND ABS(stated_total - ?) < 0.005 LIMIT 1""", (lo, hi, -p["amount"])).fetchone()
        fp = f"missing_stmt:{p['id']}"
        if stmt:
            conn.execute("UPDATE statements SET matched_bank_txn_id = ? WHERE id = ?", (p["id"], stmt["id"]))
            conn.execute("UPDATE accounts SET pays_from_account_id = ? WHERE id = ? "
                         "AND pays_from_account_id IS NULL", (p["account_id"], stmt["account_id"]))
            dx.auto_resolve(conn, fp, "matching card statement imported")
    unmatched = conn.execute(
        """SELECT t.* FROM transactions t WHERE t.kind = 'card_payment' AND t.amount < 0
           AND NOT EXISTS (SELECT 1 FROM statements s WHERE s.matched_bank_txn_id = t.id)""").fetchall()
    loose = conn.execute(
        """SELECT s.*, a.label FROM statements s JOIN accounts a ON a.id = s.account_id
           WHERE a.kind = 'card' AND s.matched_bank_txn_id IS NULL""").fetchall()
    for p in unmatched:
        near = [s for s in loose if abs((date.fromisoformat(s["billing_date"])
                                         - date.fromisoformat(p["txn_date"])).days) <= PAYMENT_WINDOW_DAYS]
        if len(near) == 1 and sum(1 for q in unmatched if q["txn_date"] == p["txn_date"]) == 1:
            s = near[0]
            dx.raise_item(
                conn, f"missing_stmt:{p['id']}", "payment_mismatch",
                f"Bank paid {_money(p['amount'])} on {p['txn_date']} but {s['label']} statement says {_money(s['stated_total'])}",
                f"Difference {_money(abs(p['amount']) - s['stated_total'])}.", "error",
                ("transaction", p["id"]), batch_id)
        else:
            dx.raise_item(
                conn, f"missing_stmt:{p['id']}", "missing_statement",
                f"Bank paid {_money(p['amount'])} on {p['txn_date']} ({p['description']}) but no card statement matches",
                "Upload the card statement for this billing date.", "warning",
                ("transaction", p["id"]), batch_id)
    cover = _bank_coverage(conn)
    for s in loose:
        fp = f"no_payment:{s['id']}"
        payer = conn.execute("SELECT pays_from_account_id FROM accounts WHERE id = ?", (s["account_id"],)).fetchone()[0]
        if payer is not None:
            has_cover = any(c["account_id"] == payer and c["period_start"] <= s["billing_date"] <= c["period_end"] for c in cover)
        else:
            has_cover = any(c["period_start"] <= s["billing_date"] <= c["period_end"] for c in cover)
        if has_cover:
            dx.raise_item(
                conn, fp, "no_bank_payment",
                f"{s['label']} statement of {_money(s['stated_total'])} billed {s['billing_date']} has no matching bank payment",
                "It may be paid from an account you have not imported.", "warning",
                ("statement", s["id"]), batch_id)
        else:
            dx.auto_resolve(conn, fp, "bank payment found")
    for s in conn.execute("SELECT id FROM statements WHERE matched_bank_txn_id IS NOT NULL"):
        dx.auto_resolve(conn, f"no_payment:{s['id']}", "bank payment found")


def track_fee_refunds(conn: sqlite3.Connection, today: str, batch_id: int | None = None) -> list[dict]:
    """Pair each refundable bank fee with a later credit of the same amount; flag late ones."""
    fees = conn.execute(
        """SELECT * FROM transactions WHERE expects_refund_by IS NOT NULL AND amount < 0
           AND NOT EXISTS (SELECT 1 FROM transactions r WHERE r.refund_of_txn_id = transactions.id)
           ORDER BY txn_date""").fetchall()
    for fee in fees:
        end = (date.fromisoformat(fee["txn_date"]) + timedelta(days=REFUND_SEARCH_DAYS)).isoformat()
        credit = conn.execute(
            """SELECT id FROM transactions WHERE account_id = ? AND amount > 0 AND refund_of_txn_id IS NULL
               AND ABS(amount + ?) < 0.005 AND txn_date BETWEEN ? AND ? ORDER BY txn_date LIMIT 1""",
            (fee["account_id"], fee["amount"], fee["txn_date"], end)).fetchone()
        if credit:
            conn.execute("UPDATE transactions SET refund_of_txn_id = ? WHERE id = ?", (fee["id"], credit["id"]))
    return fee_status(conn, today, batch_id, raise_items=True)


def fee_status(conn: sqlite3.Connection, today: str, batch_id: int | None = None,
               raise_items: bool = False) -> list[dict]:
    out = []
    for fee in conn.execute("SELECT * FROM transactions WHERE expects_refund_by IS NOT NULL AND amount < 0 ORDER BY txn_date"):
        refund = conn.execute("SELECT * FROM transactions WHERE refund_of_txn_id = ?", (fee["id"],)).fetchone()
        if refund:
            state = "refunded"
        elif today > fee["expects_refund_by"]:
            state = "overdue"
        else:
            state = "pending"
        out.append({"fee_id": fee["id"], "date": fee["txn_date"], "amount": -fee["amount"],
                    "due": fee["expects_refund_by"], "state": state,
                    "refund_date": refund["txn_date"] if refund else None})
        if raise_items:
            fp = f"fee:{fee['id']}"
            if state == "overdue":
                dx.raise_item(
                    conn, fp, "fee_unrefunded",
                    f"Bank fee {_money(fee['amount'])} on {fee['txn_date']} was not refunded by {fee['expects_refund_by']}",
                    "Previous fees of this kind were refunded. Ask the bank for this one.", "warning",
                    ("transaction", fee["id"]), batch_id)
            elif state == "refunded":
                dx.auto_resolve(conn, fp, f"refund of {_money(fee['amount'])} received on {refund['txn_date']}")
    return out


def flag_large_and_transfers(conn: sqlite3.Connection, batch_id: int) -> None:
    # Only statement imports raise flags. History imported from the old sheet is reviewed on the Review page.
    statement_batches = "(SELECT batch_id FROM source_files)"
    for t in conn.execute(f"SELECT * FROM transactions WHERE kind IN ('purchase','other') AND ABS(amount) >= ? "
                          f"AND batch_id IN {statement_batches}", (LARGE_ITEM,)):
        fp = f"large:{t['id']}"
        if t["category_status"] == "proposed":
            dx.raise_item(conn, fp, "large_item",
                          f"Large payment {_money(t['amount'])}: {t['description']} ({t['txn_date']})",
                          "Confirm or change the proposed category.", "info", ("transaction", t["id"]), batch_id)
        else:
            dx.auto_resolve(conn, fp, "category confirmed")
    for t in conn.execute(
            """SELECT t.* FROM transactions t JOIN categories c ON c.id = t.category_id
               WHERE t.kind = 'transfer' AND c.name IN ('Internal transfer','Transfer to savings')
                 AND t.batch_id IN (SELECT batch_id FROM source_files)"""):
        fp = f"xfer:{t['id']}"
        if t["counterparty_account_id"] is None:
            dx.raise_item(conn, fp, "transfer_destination",
                          f"Transfer of {_money(t['amount'])} on {t['txn_date']} has no destination account",
                          t["description"], "info", ("transaction", t["id"]), batch_id)
        else:
            dx.auto_resolve(conn, fp, "destination account set")


def run(conn: sqlite3.Connection, batch_id: int, today: str) -> None:
    match_card_payments(conn, batch_id)
    track_fee_refunds(conn, today, batch_id)
    flag_large_and_transfers(conn, batch_id)
    conn.commit()
