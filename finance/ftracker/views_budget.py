"""Budgets: the edit page and the card shown on Overview."""
import sqlite3
from html import escape as esc

from . import budgets, summary
from .charts import money
from .views import Ctx, _form, layout


def budget_card(ctx: Ctx, bs: dict[str, float], spent: dict[str, float], title: str, months: int = 1) -> str:
    rows = budgets.compare(bs, spent, months)
    if not rows:
        return ""
    tb, ts = sum(r["budget"] for r in rows), sum(r["spent"] for r in rows)

    def row(r):
        pct = min(r["spent"] / r["budget"] * 100, 100) if r["budget"] else 0
        note = f'{money(abs(r["left"]))} {"over" if r["over"] else "left"}'
        return (f'<div class="bud"><span dir="auto">{esc(r["category"])}</span>'
                f'<span class="track"><span class="fill{" over" if r["over"] else ""}" style="width:{pct:.0f}%"></span></span>'
                f'<span class="n">{money(r["spent"])} of {money(r["budget"])}<br><span class="cap {"up" if r["over"] else ""}">{note}</span></span></div>')
    edit = "" if ctx.readonly else ' <a class="cap" href="/budgets">Edit budgets</a>'
    return (f'<div class="card" style="margin-top:14px"><h4 style="margin:0 0 6px">{esc(title)}{edit}</h4>'
            f'<p class="cap" style="margin:0 0 6px">{money(ts)} spent of {money(tb)} budgeted in these categories.</p>'
            + "".join(row(r) for r in rows) + "</div>")


def budgets_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    months = summary.months_available(conn)
    complete = [m for m in months if m < ctx.today[:7]]
    use = (complete or months)[-6:]
    usual = budgets.usual(conn, use)
    current = budgets.get_all(conn)
    cats = sorted({*usual, *current} - {"Uncategorised"}, key=lambda c: (-(current.get(c) or 0), -usual.get(c, 0)))
    if not cats:
        body = '<div class="card"><p>No spending to budget yet. Import some statements first.</p></div>'
    else:
        rows = "".join(
            f'<tr><td dir="auto">{esc(c)}<input type="hidden" name="cat" value="{esc(c)}"></td>'
            f'<td class="n">{money(usual[c]) if c in usual else "–"}</td>'
            f'<td><input name="amt" inputmode="numeric" size="9" placeholder="no budget" value="{current[c]:.0f}" style="text-align:right"></td></tr>'
            if c in current else
            f'<tr><td dir="auto">{esc(c)}<input type="hidden" name="cat" value="{esc(c)}"></td>'
            f'<td class="n">{money(usual[c]) if c in usual else "–"}</td>'
            f'<td><input name="amt" inputmode="numeric" size="9" placeholder="no budget" style="text-align:right"></td></tr>' for c in cats)
        body = (_form(ctx, "/budgets/save",
                      '<div style="overflow-x:auto"><table><thead><tr><th>Category</th><th class="n">Usual per month</th><th class="n">Budget per month ₪</th></tr></thead>'
                      f'<tbody>{rows}</tbody></table></div><p class="row"><button>Save budgets</button> '
                      '<span class="cap">Leave a budget empty for a category you do not want to track.</span></p>')
                + _form(ctx, "/budgets/fill", '<button class="quiet">Fill in my usual spending, rounded up</button> '
                        f'<span class="cap">Uses the average of your last {len(use)} months with data. It replaces what is typed above.</span>', "row"))
    return layout(conn, ctx, "/budgets", "Budgets",
                  '<h1>Budgets</h1><p class="muted">Set what you want to spend on each category in a month. Overview then shows how much of each is used.</p>'
                  f'<div class="card">{body}</div>')
