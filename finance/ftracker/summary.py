"""Spending breakdowns. Expenses are the negative of cash flow; neutral categories
(card payments, transfers between your own accounts) are excluded so nothing is counted twice."""
import sqlite3


def _committed(alias: str, committed_only: bool) -> str:
    return (f" AND {alias}.batch_id IN (SELECT id FROM batches WHERE status = 'committed')" if committed_only else "")


def spending_by_category(conn: sqlite3.Connection, month: str | None = None, owner: str | None = None,
                         start: str | None = None, end: str | None = None, committed_only: bool = False):
    where = ["c.neutral = 0", "c.kind IN ('expense','fee','debt')", "t.kind != 'card_payment'"]
    args: list = []
    if month:
        where.append("t.budget_month = ?")
        args.append(month)
    if start:
        where.append("t.budget_month >= ?")
        args.append(start)
    if end:
        where.append("t.budget_month <= ?")
        args.append(end)
    if owner:
        where.append("o.name = ?")
        args.append(owner)
    return conn.execute(
        f"""SELECT COALESCE(p.name, c.name) AS category, c.name AS subcategory,
                   ROUND(SUM(-t.amount), 2) AS spent, COUNT(*) AS n
            FROM transactions t JOIN categories c ON c.id = t.category_id
            LEFT JOIN categories p ON p.id = c.parent_id
            JOIN accounts a ON a.id = t.account_id LEFT JOIN owners o ON o.id = a.owner_id
            WHERE {' AND '.join(where)} AND t.currency = 'ILS'{_committed('t', committed_only)}
            GROUP BY category, subcategory ORDER BY spent DESC""", args).fetchall()


def month_overview(conn: sqlite3.Connection, month: str, committed_only: bool = False):
    """Income, spending and savings for a month.

    Months with bank data use it. Older months that only exist in the imported budget sheet use
    the sheet's income and debt figures, because those are not in any card statement.
    """
    has_bank = conn.execute(
        """SELECT 1 FROM transactions t JOIN accounts a ON a.id = t.account_id
           WHERE t.budget_month = ? AND a.kind = 'bank'""" + _committed("t", committed_only) + """ LIMIT 1""",
        (month,)).fetchone() is not None
    spent = sum(r["spent"] for r in spending_by_category(conn, month, committed_only=committed_only))
    if has_bank:
        inc = conn.execute(
            """SELECT ROUND(COALESCE(SUM(t.amount),0),2) FROM transactions t JOIN categories c ON c.id = t.category_id
               WHERE t.budget_month = ? AND c.kind = 'income' AND t.amount > 0""" + _committed("t", committed_only),
            (month,)).fetchone()[0]
        source = "bank"
    else:
        inc = conn.execute("SELECT ROUND(COALESCE(SUM(actual),0),2) FROM monthly_entries WHERE month = ? AND section = 'income'"
                           + _committed("monthly_entries", committed_only), (month,)).fetchone()[0]
        spent += conn.execute("SELECT COALESCE(SUM(actual),0) FROM monthly_entries WHERE month = ? AND section = 'debt'"
                              + _committed("monthly_entries", committed_only), (month,)).fetchone()[0]
        source = "sheet" if inc else "none"
    saved_to = conn.execute("SELECT ROUND(COALESCE(SUM(actual),0),2) FROM monthly_entries WHERE month = ? AND section = 'saving'"
                            + _committed("monthly_entries", committed_only), (month,)).fetchone()[0]
    return {"month": month, "income": inc, "spending": round(spent, 2), "saved": round(inc - spent, 2),
            "put_into_savings": saved_to, "income_source": source}


def months_available(conn: sqlite3.Connection, committed_only: bool = False) -> list[str]:
    where = " WHERE batch_id IN (SELECT id FROM batches WHERE status = 'committed')" if committed_only else ""
    rows = conn.execute(f"SELECT budget_month FROM transactions{where} UNION SELECT month FROM monthly_entries{where} ORDER BY 1").fetchall()
    return [r[0] for r in rows]
