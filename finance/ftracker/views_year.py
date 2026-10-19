"""A whole year at a glance: every category by month, with the year before for comparison."""
import sqlite3
from html import escape as esc

from . import charts
from .charts import money
from .views import Ctx, layout
from .views_overview import _load, _qs, month_label, short_label

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def year_page(conn: sqlite3.Connection, ctx: Ctx, q: dict) -> str:
    owner = q.get("owner") or None
    d = _load(conn, owner)
    years = sorted({m[:4] for m in d["months"]})
    if not years:
        return layout(conn, ctx, "/year", "Year", '<h1>Year</h1><div class="card"><p>No data yet.</p></div>')
    y = q.get("y") if q.get("y") in years else (years[-1] if years[-1] <= ctx.today[:4] else years[-1])
    prev = str(int(y) - 1)
    months = [f"{y}-{i:02d}" for i in range(1, 13)]
    have = [m for m in months if m in d["months"]]
    cats: dict[str, float] = {}
    for m in have:
        for c, v in d["spend"].get(m, {}).items():
            cats[c] = cats.get(c, 0) + v
    prev_have = [f"{prev}-{m[5:]}" for m in have if f"{prev}-{m[5:]}" in d["months"]]           # months that have data in both years
    same = [f"{y}-{m[5:]}" for m in prev_have]
    prev_cats: dict[str, float] = {}
    this_cats: dict[str, float] = {}
    for m in prev_have:
        for c, v in d["spend"].get(m, {}).items():
            prev_cats[c] = prev_cats.get(c, 0) + v
    for m in same:
        for c, v in d["spend"].get(m, {}).items():
            this_cats[c] = this_cats.get(c, 0) + v
    inc = sum(d["inc_tot"].get(m, 0) for m in have)
    exp = sum(d["exp_tot"].get(m, 0) for m in have)
    prev_exp = sum(d["exp_tot"].get(m, 0) for m in prev_have)
    same_exp = sum(d["exp_tot"].get(m, 0) for m in same)
    rate = (inc - exp) / inc * 100 if inc else 0
    owners = [r[0] for r in conn.execute("SELECT name FROM owners ORDER BY name")]
    nav = (f'<span class="monthnav"><a class="nb" href="/year{_qs(y=prev, owner=owner)}" aria-label="Previous year">‹</a><b class="mt">{esc(y)}</b>'
           f'<a class="nb" href="/year{_qs(y=str(int(y) + 1), owner=owner)}" aria-label="Next year">›</a></span>')
    who = '<span class="seg">' + "".join(f'<a class="{"on" if (owner or "") == o else ""}" href="/year{_qs(y=y, owner=o or None)}">{esc(o or "Household")}</a>'
                                         for o in ["", *owners]) + "</span>"
    tiles = ('<div class="trio">'
             f'<div><small>Income</small><b>{money(inc)}</b></div><div><small>Expenses</small><b>{money(exp)}</b></div>'
             f'<div><small>Kept</small><b class="{"neg" if inc - exp < 0 else ""}">{money(inc - exp)}</b><small class="k">{rate:.0f}% of income</small></div></div>')
    cover = (f'<p class="cap">{len(have)} of 12 months have data: {", ".join(MONTHS[int(m[5:]) - 1] for m in have)}.'
             + (f' The last column compares {len(prev_have)} month(s) that have data in {prev} too.' if prev_have else "") + "</p>")
    chart = (charts.grouped_bars(have, [d["inc_tot"].get(m) for m in have], [d["exp_tot"].get(m) for m in have],
                                 month_href=[f'/{_qs(p="month", m=m, owner=owner)}' for m in have]) if len(have) >= 2 else "")
    ranked = sorted(cats, key=lambda c: -cats[c])
    peak = max([v for m in have for v in d["spend"].get(m, {}).values()] or [1])

    def cell(c, m):
        v = d["spend"].get(m, {}).get(c)
        if m not in d["months"]:
            return '<td class="n muted">–</td>'
        if not v:
            return '<td class="n"></td>'
        shade = min(0.55, 0.06 + v / peak * 0.5)
        return (f'<td class="n heat" style="background:color-mix(in srgb,var(--s2) {shade * 100:.0f}%,transparent)">'
                f'<a href="/{esc(_qs(p="month", m=m, open=c, owner=owner))}">{charts.compact(v)}</a></td>')
    head = "<tr><th>Category</th>" + "".join(f'<th class="n">{MONTHS[i]}</th>' for i in range(12)) + '<th class="n">Total</th><th class="n">vs last year</th></tr>'
    rows = []
    for c in ranked:
        pv = prev_cats.get(c)
        vs = ""
        if prev_have:
            delta = this_cats.get(c, 0) - (pv or 0)
            vs = (f'<span class="{"up" if delta > 0 else "down"}">{"▲" if delta > 0 else "▼"} {money(abs(delta))}</span>' if abs(delta) >= 1 else "≈")
        rows.append(f'<tr><td dir="auto">{esc(c)}</td>' + "".join(cell(c, m) for m in months) + f'<td class="n"><b>{money(cats[c])}</b></td><td class="n">{vs}</td></tr>')
    totals = ('<tr class="tot"><td><b>Total</b></td>' + "".join(f'<td class="n">{charts.compact(d["exp_tot"].get(m, 0)) if m in d["exp_tot"] else "–"}</td>' for m in months)
              + f'<td class="n"><b>{money(exp)}</b></td><td class="n">{(money(same_exp - prev_exp) if prev_have else "")}</td></tr>')
    table = f'<div class="card" style="margin-top:14px;overflow-x:auto"><table class="yeartable"><thead>{head}</thead><tbody>{"".join(rows)}{totals}</tbody></table></div>'
    return layout(conn, ctx, "/year", f"Year {y}", f'<h1>Year {esc(y)}</h1><div class="modes">{nav}{who}<a class="btnlink" href="/">Back to Overview</a></div>{tiles}{cover}'
                  f'<div class="card" style="margin-top:6px">{chart}</div>{table}'
                  '<p class="cap">Click an amount to see that month’s payments. Darker means more spent in that category.</p>')
