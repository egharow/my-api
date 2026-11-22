"""Recurring payments and alerts."""
import sqlite3
from html import escape as esc

from . import alerts, recurring, regulars, summary
from .charts import money
from .views import Ctx, _form, dmy, layout


def _status(i: dict) -> str:
    chips = []
    if not i["active"]:
        chips.append('<span class="chip">stopped?</span>')
    elif i["new"]:
        chips.append('<span class="chip">new</span>')
    if i["active"] and abs(i["change"]) >= 0.08 and i["prev_amount"] >= 10:
        chips.append(f'<span class="chip {"serious" if i["change"] > 0 else "good"}"><b>{i["change"] * 100:+.0f}%</b></span>')
    return " ".join(chips)


def _row(i: dict) -> str:
    cad = "every month" if i["cadence"] == "monthly" else "every year"
    return (f'<tr><td dir="auto"><a href="/?find={esc(i["name"][:24])}"><bdi dir="auto">{esc(i["name"])}</bdi></a> {_status(i)}'
            f'<div class="cap">{esc(i["category"])} · {cad}</div></td><td class="n">{money(i["usual"], "ILS", 2)}</td>'
            f'<td class="n">{esc(dmy(i["last_date"]))}</td><td class="n">{esc(dmy(i["next"])) if i["active"] else "–"}</td><td class="n">{money(i["yearly_cost"])}</td></tr>')


STATUS = {
    "ok": ('<span class="chip good"><b>✓</b> arrived</span>', "arrived"),
    "different": ('<span class="chip serious"><b>▲</b> different</span>', "different"),
    "missing": ('<span class="chip serious"><b>!</b> missing</span>', "missing"),
    "not yet": ('<span class="chip">not yet</span>', "waiting"),
    "no history": ('<span class="chip">no history yet</span>', "no history"),
}


def _expected_rows(conn: sqlite3.Connection, today: str) -> list[dict]:
    return [e for e in regulars.evaluate(conn, today) if e["month"] == today[:7]]


def _expected_section(conn: sqlite3.Connection, ctx: Ctx) -> str:
    rows = []
    for e in _expected_rows(conn, ctx.today):
        it = e["item"]
        chip = STATUS[e["status"]][0]
        if e["status"] == "ok" or e["status"] == "different":
            what = money(e["this"], "ILS", 2)
        else:
            what = "–"
        gone = _form(ctx, "/regular/remove", f'<input type="hidden" name="id" value="{it["id"]}"><button class="quiet">Stop tracking</button>', "inline")
        diff = (f' <span class="cap">{e["diff"]:+,.2f} from usual</span>' if e["status"] == "different" else "")
        rows.append(f'<tr><td dir="auto"><b>{esc(it["name"])}</b><div class="cap">{"money in" if it["direction"] == "in" else "money out"}</div></td>'
                    f'<td class="n">{money(e["usual"], "ILS", 2) if e["usual"] else "–"}</td><td class="n">{what}{diff}</td>'
                    f'<td class="n">{("around the " + str(e["day"]) + "th") if e["day"] else "–"}</td><td>{chip}</td><td>{gone}</td></tr>')
    table = (f'<div style="overflow-x:auto"><table><thead><tr><th>Item</th><th class="n">Usual</th><th class="n">This month</th><th class="n">Usually</th><th>Status</th><th></th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div>') if rows else '<p class="muted">Nothing tracked yet. Add something below, or pick from what the app found.</p>'
    months = summary.months_available(conn)
    sug = regulars.suggestions(conn, months[-1], months) if months else []
    sug_html = ""
    if sug:
        sug_html = ('<h3 style="margin:14px 0 4px;font-size:15px">The app sees these coming every month</h3>'
                    + "".join(_form(ctx, "/regular/add", f'<input type="hidden" name="name" value="{esc(s["name"])}"><input type="hidden" name="pattern" value="{esc(s["pattern"])}">'
                              f'<input type="hidden" name="direction" value="{s["direction"]}"><b dir="auto">{esc(s["name"])}</b> '
                              f'<span class="cap">{"money in" if s["direction"] == "in" else "money out"} · usually {money(s["usual"], "ILS", 2)} · {s["months"]} of the last 6 months</span> '
                              '<button class="quiet">Track it</button>', "row") for s in sug[:12]))
    add = _form(ctx, "/regular/add",
                '<input name="name" placeholder="Name, for example קצבת ילדים" required> <input name="pattern" placeholder="Words on the payment (if different)"> '
                '<select name="direction"><option value="in">money coming in</option><option value="out">money going out</option></select> '
                '<input name="amount" inputmode="decimal" placeholder="Usual amount (optional)" size="14"> '
                '<label class="cap">flag if it differs by more than <input name="tolerance" value="8" size="3" inputmode="decimal">%</label> <button>Track it</button>', "row")
    return ('<section class="card" id="expected" style="margin-bottom:14px"><h2>Expected every month</h2>'
            '<p class="muted">Things that should come in, or be paid, every month, like child allowance or rent. The app flags one that has not arrived '
            'a few days after its usual day, and one that comes out different from usual. Leave the amount empty to use your earlier months.</p>'
            + table + sug_html + '<h3 style="margin:14px 0 4px;font-size:15px">Track something new</h3>' + add + "</section>")


def recurring_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    items = recurring.detect(conn, ctx.today)
    active = [i for i in items if i["active"]]
    bills = [i for i in active if not i["debt"]]
    debts = [i for i in active if i["debt"]]
    stopped = [i for i in items if not i["active"]]
    month = sum(i["usual"] for i in bills if i["cadence"] == "monthly")
    year = sum(i["yearly_cost"] for i in bills)
    head = ('<thead><tr><th>Payment</th><th class="n">Usual</th><th class="n">Last charged</th><th class="n">Next expected</th><th class="n">Per year</th></tr></thead>')

    def table(rows):
        return f'<div style="overflow-x:auto"><table>{head}<tbody>{"".join(_row(i) for i in rows)}</tbody></table></div>'
    body = (f'<div class="trio"><div><small>Bills and subscriptions, per month</small><b>{money(month)}</b></div>'
            f'<div><small>Per year</small><b>{money(year)}</b></div><div><small>Loans and mortgage, per month</small><b>{money(sum(i["usual"] for i in debts))}</b></div></div>')
    body += f'<div class="card" style="margin-top:14px"><h2>Bills and subscriptions</h2>{table(bills) if bills else "<p class=muted>Nothing found yet. It needs a few months of statements.</p>"}</div>'
    if debts:
        body += f'<div class="card" style="margin-top:14px"><h2>Loans and mortgage</h2>{table(debts)}</div>'
    if stopped:
        body += f'<details class="card" style="margin-top:14px"><summary><b>Stopped or not seen lately</b> <span class="cap">{len(stopped)}</span></summary>{table(stopped)}</details>'
    body += '<p class="cap" style="margin-top:12px">Found from your own payments: the same merchant, about once a month, about the same amount. A “stopped?” mark means it has not come for a while.</p>'
    return layout(conn, ctx, "/recurring", "Recurring", f"<h1>Recurring payments</h1>{_expected_section(conn, ctx)}{body}")


def alerts_card(conn: sqlite3.Connection, ctx: Ctx, limit: int = 4) -> str:
    if ctx.readonly:
        return ""
    items = alerts.build(conn, ctx.today)
    if not items:
        return ""
    rows = "".join(
        f'<div class="alert {esc(a["severity"])}"><span class="dot"></span><span><b dir="auto">{esc(a["title"])}</b><span class="cap"> {esc(a["detail"])}</span></span>'
        f'<span><a href="{esc(a["link"])}">Open</a></span></div>' for a in items[:limit])
    more = f'<p class="cap"><a href="/alerts">See all {len(items)} alerts</a></p>' if len(items) > limit else ""
    return f'<div class="card" style="margin-top:14px"><h4 style="margin:0 0 6px">Worth knowing</h4>{rows}{more}</div>'


def alerts_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    items = alerts.build(conn, ctx.today)
    rows = "".join(
        f'<div class="alert {esc(a["severity"])}"><span class="dot"></span><span><b dir="auto">{esc(a["title"])}</b><span class="cap"> {esc(a["detail"])}</span></span><span class="row">'
        f'<a href="{esc(a["link"])}">Open</a> '
        + _form(ctx, "/alerts/dismiss", f'<input type="hidden" name="id" value="{esc(a["id"])}"><button class="quiet">Dismiss</button>', "inline") + "</span></div>"
        for a in items)
    body = rows or "<p>Nothing needs your attention. ✔</p>"
    return layout(conn, ctx, "/alerts", "Alerts", f'<h1>Alerts</h1><div class="card">{body}</div>'
                  '<p class="cap">Price changes, missing or new recurring payments, charges that look doubled, unrefunded bank fees, missing statements and budgets that are over.</p>')
