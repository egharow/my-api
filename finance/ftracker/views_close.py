"""The 'Close the month' page."""
import sqlite3
from html import escape as esc

from . import closing, summary
from .charts import money
from .views import Ctx, _form, layout
from .views_overview import month_label


def close_page(conn: sqlite3.Connection, ctx: Ctx, q: dict) -> str:
    m = q.get("m") or closing.to_close(conn, ctx.today)
    if not m:
        return layout(conn, ctx, "/close", "Close the month",
                      '<h1>Close the month</h1><div class="card"><p>Every finished month is already closed. ✔</p><p><a href="/">Back to Overview</a></p></div>')
    cs = closing.checks(conn, m, ctx.today)
    ok = all(c["ok"] for c in cs)
    is_closed = m in closing.closed_months(conn)
    ov = summary.month_overview(conn, m)
    def row(c):
        fix = "" if c["ok"] else '<a class="btnlink" href="' + esc(c["link"]) + '">Fix</a>'
        return (f'<div class="chk {"ok" if c["ok"] else "todo"}"><span class="mark">{"✔" if c["ok"] else "!"}</span>'
                f'<span><b>{esc(c["label"])}</b><span class="cap"> {esc(c["detail"])}</span></span><span>{fix}</span></div>')
    rows = "".join(row(c) for c in cs)
    kept = ov["saved"]
    summ = (f'<div class="trio"><div><small>Income</small><b>{money(ov["income"])}</b></div>'
            f'<div><small>Spent</small><b>{money(ov["spending"])}</b></div>'
            f'<div><small>Kept</small><b>{money(kept)}</b></div></div>')
    if is_closed:
        action = _form(ctx, "/close/do", f'<input type="hidden" name="m" value="{esc(m)}"><input type="hidden" name="reopen" value="1">'
                       '<span class="chip good"><b>✔</b> closed</span> <button class="quiet">Reopen</button>', "row")
    else:
        warn = "" if ok else '<p class="cap">Some checks are open. You can still close it and fix them later.</p>'
        action = warn + _form(ctx, "/close/do", f'<input type="hidden" name="m" value="{esc(m)}">'
                              f'<button>{"Close " + esc(month_label(m)) if ok else "Close it anyway"}</button>', "row")
    return layout(conn, ctx, "/close", "Close the month",
                  f'<h1>Close {esc(month_label(m))}</h1><div class="card">{rows}</div>'
                  f'<div style="margin:14px 0">{summ}</div><div class="card">{action}</div>')
