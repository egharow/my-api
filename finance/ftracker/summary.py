"""Spending breakdowns. Expenses are the negative of cash flow; neutral categories
(card payments, transfers between your own accounts) are excluded so nothing is counted twice."""
import sqlite3


def spending_by_category(conn: sqlite3.Connection, month: str | None = None, owner: str | None = None,
                         start: str | None = None, end: str | None = None):
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
            WHERE {' AND '.join(where)} AND t.currency = 'ILS'
            GROUP BY category, subcategory ORDER BY spent DESC""", args).fetchall()


def month_overview(conn: sqlite3.Connection, month: str):
    """Income, spending and savings for a month.

    Months with bank data use it. Older months that only exist in the imported budget sheet use
    the sheet's income and debt figures, because those are not in any card statement.
    """
    has_bank = conn.execute(
        """SELECT 1 FROM transactions t JOIN accounts a ON a.id = t.account_id
           WHERE t.budget_month = ? AND a.kind = 'bank' LIMIT 1""", (month,)).fetchone() is not None
    spent = sum(r["spent"] for r in spending_by_category(conn, month))
    if has_bank:
        inc = conn.execute(
            """SELECT ROUND(COALESCE(SUM(t.amount),0),2) FROM transactions t JOIN categories c ON c.id = t.category_id
               WHERE t.budget_month = ? AND c.kind = 'income' AND t.amount > 0""", (month,)).fetchone()[0]
        source = "bank"
    else:
        inc = conn.execute("SELECT ROUND(COALESCE(SUM(actual),0),2) FROM monthly_entries WHERE month = ? AND section = 'income'",
                           (month,)).fetchone()[0]
        spent += conn.execute("SELECT COALESCE(SUM(actual),0) FROM monthly_entries WHERE month = ? AND section = 'debt'",
                              (month,)).fetchone()[0]
        source = "sheet" if inc else "none"
    saved_to = conn.execute("SELECT ROUND(COALESCE(SUM(actual),0),2) FROM monthly_entries WHERE month = ? AND section = 'saving'",
                            (month,)).fetchone()[0]
    return {"month": month, "income": inc, "spending": round(spent, 2), "saved": round(inc - spent, 2),
            "put_into_savings": saved_to, "income_source": source}
