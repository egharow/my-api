"""Balances you type in (pension, savings, investments, property...) and net worth from them."""
import sqlite3
from datetime import date, timedelta

from . import fx
from .accounts import find_account
from .db import audit, now

DUE_DAYS = 62      # about two months
STALE_DAYS = 60
ASSET_KINDS = ("bank", "investment", "pension", "savings", "real_estate", "loan", "other")


def set_balance(conn: sqlite3.Connection, ref: str, amount: float, as_of: str | None = None,
                currency: str | None = None, batch_id: int | None = None, note: str | None = None) -> None:
    set_balance_for(conn, find_account(conn, ref)["id"], amount, as_of, currency, batch_id, note)


def set_balance_for(conn: sqlite3.Connection, account_id: int, amount: float, as_of: str | None = None,
                    currency: str | None = None, batch_id: int | None = None, note: str | None = None) -> None:
    acct = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    as_of = as_of or date.today().isoformat()
    if acct["kind"] == "loan" and amount > 0:
        amount = -amount           # debts are stored as negative balances
    conn.execute(
        """INSERT INTO balances (account_id, as_of, amount, currency, batch_id, note, created_at)
           VALUES (?,?,?,?,?,?,?) ON CONFLICT(account_id, as_of)
           DO UPDATE SET amount = excluded.amount, currency = excluded.currency, note = excluded.note""",
        (acct["id"], as_of, amount, (currency or acct["currency"]).upper(), batch_id, note, now()))
    audit(conn, "set_balance", "account", acct["id"], f"{amount} on {as_of}")
    conn.commit()


def latest(conn: sqlite3.Connection, as_of: str | None = None):
    as_of = as_of or date.today().isoformat()
    return conn.execute(
        """SELECT a.id AS account_id, a.label, a.kind, a.currency AS account_currency, o.name AS owner,
                  b.as_of, b.amount, b.currency
           FROM accounts a LEFT JOIN owners o ON o.id = a.owner_id
           JOIN balances b ON b.id = (SELECT id FROM balances WHERE account_id = a.id AND as_of <= ?
                                      ORDER BY as_of DESC LIMIT 1)
           WHERE a.active = 1 AND a.kind != 'card' ORDER BY a.kind, a.label""", (as_of,)).fetchall()


def net_worth(conn: sqlite3.Connection, as_of: str | None = None, in_currency: str = "ILS",
              owner: str | None = None) -> dict:
    as_of = as_of or date.today().isoformat()
    total, lines = 0.0, []
    for r in latest(conn, as_of):
        if owner and r["owner"] != owner:
            continue
        value = fx.convert_asset(conn, r["amount"], r["currency"], in_currency, as_of)
        total += value
        lines.append({"account": r["label"], "kind": r["kind"], "owner": r["owner"],
                      "as_of": r["as_of"], "native": r["amount"], "currency": r["currency"], "value": value})
    return {"as_of": as_of, "currency": in_currency, "total": total, "lines": lines}


def stale(conn: sqlite3.Connection, today: str, days: int = STALE_DAYS):
    cutoff = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    return [r for r in latest(conn, today) if r["as_of"] < cutoff]


def balances_due(conn: sqlite3.Connection, today: str) -> tuple[bool, str | None]:
    """Due when no balance has been entered for about two months (or ever)."""
    row = conn.execute(
        """SELECT MAX(b.as_of) FROM balances b JOIN accounts a ON a.id = b.account_id
           WHERE a.kind != 'bank'""").fetchone()
    last = row[0]
    if last is None:
        return True, None
    return (date.fromisoformat(today) - date.fromisoformat(last)).days > DUE_DAYS, last
