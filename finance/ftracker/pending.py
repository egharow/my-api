"""Pending card charges: what the card sites show as not final yet. Shown so you see them early, never counted in totals."""
import re
import sqlite3

from .parsers.base import ParsedStatement
from .parsers.cardfetch import ISSUERS, NAMES, _currency, _day

STALE_DAYS = 14          # a pending charge not seen again by a fetch for this long is dropped


def replace(conn: sqlite3.Connection, login_id: str, company: str, accounts: list, fetched_at: str) -> int:
    """Replace this login's pending charges with what the site showed in this fetch. Returns how many are pending now."""
    from .importer import ensure_account
    conn.execute("DELETE FROM pending_charges WHERE login_id = ?", (login_id,))
    n = 0
    for a in accounts or []:
        last4 = re.sub(r"\D", "", str(a.get("accountNumber") or ""))[-4:]
        rows = [t for t in a.get("txns") or [] if t.get("status") == "pending" and t.get("chargedAmount") is not None]
        if not last4 or not rows:
            continue
        acct = ensure_account(conn, ParsedStatement(issuer=ISSUERS[company], account_kind="card", last4=last4, product=NAMES[company],
                                                    billing_date=None, period_start=None, period_end=None, stated_total=None, currency="ILS"))
        for t in rows:
            when = _day(t.get("date"))
            if not when:
                continue
            cur = _currency(t.get("chargedCurrency"))
            ocur = _currency(t.get("originalCurrency"), cur)
            conn.execute("""INSERT INTO pending_charges (login_id, account_id, txn_date, description, amount, currency, orig_amount, orig_currency, fetched_at)
                            VALUES (?,?,?,?,?,?,?,?,?)""",
                         (login_id, acct, when.isoformat(), (t.get("description") or "").strip() or "(no description)",
                          round(float(t["chargedAmount"]), 2), cur,
                          t.get("originalAmount") if ocur != cur else None, ocur if ocur != cur else None, fetched_at))
            n += 1
    return n


def current(conn: sqlite3.Connection, today: str) -> list:
    """Pending charges still worth showing, newest first."""
    return conn.execute(
        """SELECT p.*, a.label AS account FROM pending_charges p LEFT JOIN accounts a ON a.id = p.account_id
           WHERE julianday(?) - julianday(substr(p.fetched_at, 1, 10)) <= ? ORDER BY p.txn_date DESC, p.id DESC""",
        (today, STALE_DAYS)).fetchall()


def total(rows) -> float:
    return round(-sum(r["amount"] for r in rows if r["currency"] == "ILS"), 2)
