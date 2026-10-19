"""Trips and tags, and payments added by hand."""
import sqlite3
from html import escape as esc

from . import cash, trips
from .charts import money
from .views import Ctx, _form, dmy, layout


def _cat_options(conn: sqlite3.Connection, selected: str = "") -> str:
    return "".join(f'<option{" selected" if c[0] == selected else ""}>{esc(c[0])}</option>'
                   for c in conn.execute("SELECT name FROM categories WHERE name != 'Uncategorised' AND kind IN ('expense','fee') ORDER BY name"))


def _tag_options(conn: sqlite3.Connection) -> str:
    return '<option value="">no trip or tag</option>' + "".join(
        f'<option value="{t["id"]}">{esc(t["name"])}</option>' for t in trips.all_tags(conn))


def cash_form(conn: sqlite3.Connection, ctx: Ctx, today: str, tag_id: int | None = None, back: str = "/cash") -> str:
    tags = _tag_options(conn).replace(f'value="{tag_id}"', f'value="{tag_id}" selected') if tag_id else _tag_options(conn)
    return _form(ctx, "/cash/add",
                 f'<input type="hidden" name="back" value="{esc(back)}">'
                 f'<label>Day <input type="date" name="when" value="{esc(today)}" required></label> '
                 '<label>Amount ₪ <input name="amount" inputmode="decimal" size="8" required></label> '
                 '<label>What for <input name="description" placeholder="for example taxi, market" required></label> '
                 f'<label>Category <select name="category" required>{_cat_options(conn)}</select></label> '
                 f'<label>Trip or tag <select name="tag">{tags}</select></label> <button>Add payment</button>', "row")


def cash_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    rows = "".join(
        f'<tr><td>{esc(dmy(r["txn_date"]))}</td><td dir="auto">{esc(r["description"])}</td><td dir="auto">{esc(r["category"])}</td>'
        f'<td class="n">{money(r["spent"], "ILS", 2)}</td><td>'
        + _form(ctx, "/cash/delete", f'<input type="hidden" name="id" value="{r["id"]}"><button class="quiet">Delete</button>', "inline") + "</td></tr>"
        for r in cash.recent(conn))
    table = (f'<table><thead><tr><th>Day</th><th>What for</th><th>Category</th><th class="n">Amount</th><th></th></tr></thead><tbody>{rows}</tbody></table>'
             if rows else '<p class="muted">Nothing added by hand yet.</p>')
    return layout(conn, ctx, "/trips", "Cash payments",
                  '<h1>Add a payment by hand</h1><p class="muted">For cash and anything that is not on a statement. It counts like any other payment, in the month of the day you choose.</p>'
                  f'<div class="card">{cash_form(conn, ctx, ctx.today)}</div><div class="card" style="margin-top:14px"><h2>Added by hand</h2>{table}</div>')


def trips_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    cards = []
    for t in trips.all_tags(conn):
        s = trips.summary(conn, t["id"])
        when = f'{dmy(t["start_date"])} to {dmy(t["end_date"])}' if t["start_date"] else "a tag"
        per = f' · {money(s["per_day"])} a day' if s["per_day"] else ""
        cards.append(f'<a class="card tripcard" href="/trip/{t["id"]}"><b dir="auto">{esc(t["name"])}</b>'
                     f'<span class="cap">{esc(when)}</span><span class="tripamt">{money(s["total"])}</span>'
                     f'<span class="cap">{len(s["lines"])} payment(s){esc(per)}</span></a>')
    body = "".join(cards) or '<div class="card"><p>No trips yet. Add one below, for example “Thailand” with its first and last day, and everything you spent in those days is collected for you.</p></div>'
    new_trip = _form(ctx, "/trips/new", '<input type="hidden" name="kind" value="trip"><label>Name <input name="name" required placeholder="Thailand"></label> '
                     '<label>First day <input type="date" name="start" required></label> <label>Last day <input type="date" name="end" required></label> '
                     '<button>Add trip</button>', "row")
    new_tag = _form(ctx, "/trips/new", '<input type="hidden" name="kind" value="tag"><label>Name <input name="name" required placeholder="renovation"></label> '
                    '<button class="quiet">Add tag</button> <span class="cap">A tag collects only the payments you add to it.</span>', "row")
    return layout(conn, ctx, "/trips", "Trips",
                  '<h1>Trips and tags</h1>'
                  f'<div class="tripgrid">{body}</div>'
                  f'<div class="card" style="margin-top:14px"><h2>New trip</h2>{new_trip}</div>'
                  f'<div class="card" style="margin-top:14px"><h2>New tag</h2>{new_tag}</div>'
                  '<p style="margin-top:14px"><a class="btnlink" href="/cash">Add a cash payment</a></p>')


def trip_page(conn: sqlite3.Connection, ctx: Ctx, tag_id: int, q: dict) -> str:
    s = trips.summary(conn, tag_id)
    tag = s["tag"]
    if not tag:
        return layout(conn, ctx, "/trips", "Trip", '<h1>Not found</h1><p><a href="/trips">Back to trips</a></p>')
    when = f'{dmy(tag["start_date"])} to {dmy(tag["end_date"])} ({s["days"]} days)' if tag["start_date"] else "Only the payments you add"
    tiles = ('<div class="trio">'
             f'<div><small>Total</small><b>{money(s["total"])}</b></div>'
             f'<div><small>{"Per day" if s["per_day"] else "Payments"}</small><b>{money(s["per_day"]) if s["per_day"] else len(s["lines"])}</b></div>'
             f'<div><small>Charged in another currency</small><b>{money(s["foreign"])}</b></div></div>')
    cats = "".join(f'<div class="srcrow"><span dir="auto">{esc(c)}</span><span class="n">{money(v)} <span class="cap">{v / (s["total"] or 1) * 100:.0f}%</span></span></div>'
                   for c, v in s["by_category"])

    def row(r):
        undo = _form(ctx, "/trip/remove", f'<input type="hidden" name="tag" value="{tag_id}"><input type="hidden" name="id" value="{r["id"]}">'
                     '<button class="quiet" title="Take it out of this trip">✕</button>', "inline")
        foreign = r["orig_currency"] not in (None, "ILS") and r["orig_amount"]
        fx = (' <span class="cap">' + esc("{:,.0f} {}".format(abs(r["orig_amount"]), r["orig_currency"])) + "</span>") if foreign else ""
        mark = ' <span class="chip">added</span>' if r["added"] else ""
        return (f'<tr><td>{esc(dmy(r["txn_date"]))}</td><td dir="auto">{esc(r["description"])}{mark}</td><td dir="auto">{esc(r["category"])}</td>'
                f'<td class="n">{money(r["spent"], "ILS", 2)}{fx}</td><td>{undo}</td></tr>')
    table = (f'<div style="overflow-x:auto"><table><thead><tr><th>Day</th><th>Payment</th><th>Category</th><th class="n">Amount</th><th></th></tr></thead>'
             f'<tbody>{"".join(row(r) for r in s["lines"])}</tbody></table></div>' if s["lines"] else '<p class="muted">Nothing here yet.</p>')
    find = (q.get("find") or "").strip()
    found = ""
    if find:
        hits = trips.search_lines(conn, find)
        have = {r["id"] for r in s["lines"]}
        items = "".join(
            f'<label style="display:flex;gap:8px;padding:4px 0"><input type="checkbox" name="line" value="{h["id"]}"{" disabled" if h["id"] in have else ""}> '
            f'<span>{esc(dmy(h["txn_date"]))} · <bdi dir="auto">{esc(h["description"])}</bdi> · {money(h["spent"], "ILS", 2)} · {esc(h["category"])}'
            f'{" (already here)" if h["id"] in have else ""}</span></label>' for h in hits)
        empty = "" if items else '<p class="muted">No payments found.</p>'
        found = _form(ctx, "/trip/add", f'<input type="hidden" name="tag" value="{tag_id}">{items}{empty}' + ("<button>Add the ticked payments</button>" if hits else ""))
    add_box = ('<div class="card" style="margin-top:14px"><h2>Add payments to it</h2>'
               '<p class="cap">For flights or hotels you paid before the trip, or anything outside its days. Search by name.</p>'
               f'<form method="get" action="/trip/{tag_id}" class="row"><input name="find" value="{esc(find)}" placeholder="for example El Al"> <button class="quiet">Search</button></form>{found}</div>')
    cash_box = f'<div class="card" style="margin-top:14px"><h2>Add a cash payment</h2>{cash_form(conn, ctx, tag["start_date"] or ctx.today, tag_id, f"/trip/{tag_id}")}</div>'
    delete = _form(ctx, "/trip/delete", f'<input type="hidden" name="tag" value="{tag_id}"><button class="quiet">Delete this {esc(tag["kind"])}</button> '
                   '<span class="cap">The payments stay in your records.</span>', "row")
    return layout(conn, ctx, "/trips", tag["name"],
                  f'<p><a href="/trips">← Trips and tags</a></p><h1 dir="auto">{esc(tag["name"])}</h1><p class="muted">{esc(when)}</p>{tiles}'
                  f'<div class="grid"><div class="card"><h2>By category</h2>{cats or "<p class=muted>Nothing yet.</p>"}</div>'
                  f'<div class="card"><h2>Payments</h2>{table}</div></div>{add_box}{cash_box}<p style="margin-top:14px">{delete}</p>')
