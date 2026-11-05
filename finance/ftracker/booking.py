"""Which month a card purchase counts in: the month you bought it, or the month the card is billed."""
import sqlite3

KEY = "card_basis"
BASES = ("billing", "purchase")


def basis(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()
    return row[0] if row and row[0] in BASES else "billing"


def month_for(chosen: str, txn_date: str, billing_date: str | None, instalment_total: int | None = None) -> str:
    """A purchase counts where you made it. A payment in instalments keeps counting in the month each instalment is billed."""
    if chosen == "purchase" and not (instalment_total and instalment_total > 1):
        return txn_date[:7]
    return (billing_date or txn_date)[:7]


def set_basis(conn: sqlite3.Connection, chosen: str) -> int:
    """Change the setting and move every card payment to its month. Returns how many were moved."""
    if chosen not in BASES:
        raise ValueError("Choose the month you bought it or the month the card is billed.")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (KEY, chosen))
    if chosen == "purchase":
        sql = """UPDATE transactions SET budget_month = substr(txn_date, 1, 7)
                 WHERE account_id IN (SELECT id FROM accounts WHERE kind = 'card') AND billing_date IS NOT NULL
                   AND COALESCE(instalment_total, 0) <= 1 AND budget_month != substr(txn_date, 1, 7)"""
    else:
        sql = """UPDATE transactions SET budget_month = substr(billing_date, 1, 7)
                 WHERE account_id IN (SELECT id FROM accounts WHERE kind = 'card') AND billing_date IS NOT NULL
                   AND budget_month != substr(billing_date, 1, 7)"""
    n = conn.execute(sql).rowcount
    conn.commit()
    return n


def repair_continued_charges(conn: sqlite3.Connection) -> int:
    """A payment in instalments shows the same purchase date on every statement. When a card line has the same date, merchant
    and amount as a line on another statement of the same card, it is one more instalment of that purchase, so it counts in
    the month it is billed (as marked instalments already do), not in the month of the original purchase. Returns lines moved."""
    if basis(conn) != "purchase":
        return 0
    n = conn.execute(
        """UPDATE transactions SET budget_month = substr(billing_date, 1, 7)
           WHERE account_id IN (SELECT id FROM accounts WHERE kind = 'card') AND billing_date IS NOT NULL
             AND statement_id IS NOT NULL AND COALESCE(instalment_total, 0) <= 1
             AND budget_month != substr(billing_date, 1, 7)
             AND EXISTS (SELECT 1 FROM transactions o WHERE o.account_id = transactions.account_id AND o.txn_date = transactions.txn_date
                         AND o.description_norm = transactions.description_norm AND o.amount = transactions.amount
                         AND o.statement_id IS NOT NULL AND o.statement_id != transactions.statement_id)""").rowcount
    if n:
        conn.commit()
    return n
