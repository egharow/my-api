"""Where every statement file went: which account, which dates, how many payments, which months they count in."""
import sqlite3
from html import escape as esc

from .charts import money
from .views import Ctx, dmy, layout


def files_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    rows = conn.execute(
        """SELECT s.id, f.original_name, f.imported_at, f.archived_path, a.label, a.kind, a.issuer, s.billing_date, s.period_start, s.period_end,
                  s.stated_total, s.matched_bank_txn_id, b.status AS batch_status,
                  (SELECT COUNT(*) FROM transactions t WHERE t.statement_id = s.id) AS n,
                  (SELECT ROUND(SUM(-t.amount), 2) FROM transactions t WHERE t.statement_id = s.id) AS spent,
                  (SELECT group_concat(m || ' (' || c || ')', ', ') FROM (SELECT t.budget_month AS m, COUNT(*) AS c FROM transactions t WHERE t.statement_id = s.id
                                                                          GROUP BY t.budget_month ORDER BY t.budget_month)) AS months,
                  (SELECT MIN(t.txn_date) FROM transactions t WHERE t.statement_id = s.id) AS d0,
                  (SELECT MAX(t.txn_date) FROM transactions t WHERE t.statement_id = s.id) AS d1
           FROM statements s JOIN source_files f ON f.id = s.source_file_id JOIN accounts a ON a.id = s.account_id
           LEFT JOIN batches b ON b.id = f.batch_id ORDER BY a.kind, a.label, COALESCE(s.billing_date, s.period_start)""").fetchall()
    body = []
    for r in rows:
        if r["kind"] == "card":
            when = f'Billed {dmy(r["billing_date"])}<div class="cap">Purchases {dmy(r["d0"])} to {dmy(r["d1"])}</div>'
            ok = r["stated_total"] is not None and abs((r["spent"] or 0) - r["stated_total"]) < 0.01
            check = (f'{money(r["stated_total"], "ILS", 2)} <span class="chip good"><b>✔</b> adds up</span>' if ok else
                     f'{money(r["stated_total"] or 0, "ILS", 2)} stated, {money(r["spent"] or 0, "ILS", 2)} read <span class="chip serious"><b>!</b></span>')
            paid = "—"
            if r["matched_bank_txn_id"]:
                p = conn.execute("SELECT t.txn_date, -t.amount AS amt, a.label FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.id = ?", (r["matched_bank_txn_id"],)).fetchone()
                paid = f'{dmy(p["txn_date"])} · {money(p["amt"], "ILS", 2)} · {esc(p["label"])}' if p else "—"
            else:
                paid = '<span class="cap">no bank payment found yet</span>'
        else:
            when = f'{dmy(r["period_start"])} to {dmy(r["period_end"])}<div class="cap">Movements {dmy(r["d0"])} to {dmy(r["d1"])}</div>'
            check = f'{money(r["spent"] or 0, "ILS", 2)} out in all'
            paid = "—"
        body.append(f'<tr><td dir="auto"><b>{esc(r["label"])}</b><div class="cap">{esc(r["issuer"])}</div></td><td>{when}</td><td class="n">{r["n"]}</td>'
                    f'<td class="n">{check}</td><td>{paid}</td><td class="cap">{esc(r["months"] or "")}</td>'
                    f'<td class="cap" dir="auto">{esc(r["original_name"])}<div>{esc(r["batch_status"] or "")}</div></td></tr>')
    table = ('<div class="card" style="overflow-x:auto"><table><thead><tr><th>Account</th><th>Dates</th><th class="n">Payments</th><th class="n">Total</th>'
             '<th>Paid from your bank</th><th>Counted in</th><th>File</th></tr></thead><tbody>' + "".join(body) + "</tbody></table></div>") if body else \
            '<div class="card"><p>No statement files yet.</p></div>'
    return layout(conn, ctx, "/files", "Where every file went",
                  '<h1>Where every file went</h1><p class="muted">Each statement you added: the account it belongs to, its dates, how many payments it holds, '
                  'whether its total matches what the card company states, the bank payment that settled it, and the months its payments count in.</p>' + table)
