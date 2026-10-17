"""The page you open the app on: what came in, what went out, what you kept, and where it went.

One page, two ways to look: a single month, or a period (6 months, 12 months, or a range you choose) to see averages.
"""
import re
import sqlite3
from datetime import date
from html import escape as esc

from . import balances, budgets, charts, closing, expected, insights, summary
from .charts import money
from .views_budget import budget_card
from .views import Ctx, _form, counts, dmy, layout

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
USUAL_MONTHS = 6


def month_label(m: str) -> str:
    return f"{MONTHS[int(m[5:7]) - 1]} {m[:4]}"


def short_label(m: str) -> str:
    return f"{MONTHS[int(m[5:7]) - 1][:3]} {m[2:4]}"


def shift(m: str, n: int) -> str:
    y, mo = int(m[:4]), int(m[5:7]) - 1 + n
    return f"{y + mo // 12:04d}-{mo % 12 + 1:02d}"


def months_between(a: str, b: str) -> list[str]:
    out, m = [], a
    while m <= b:
        out.append(m)
        m = shift(m, 1)
    return out


def _qs(**kw) -> str:
    return "?" + "&".join(f"{k}={esc(str(v))}" for k, v in kw.items() if v not in (None, ""))


def _load(conn: sqlite3.Connection, owner: str | None) -> dict:
    avail = summary.months_available(conn)
    spend: dict[str, dict[str, float]] = {}
    for m in avail:
        rows = summary.spending_by_category(conn, m, owner)
        if rows:
            d: dict[str, float] = {}
            for r in rows:
                d[r["category"]] = round(d.get(r["category"], 0) + r["spent"], 2)
            spend[m] = d
    inc = summary.income_by_source(conn, avail, owner)
    inc_tot = {m: round(sum(d.values()), 2) for m, d in inc.items()}
    data_months = sorted(set(spend) | set(inc_tot))
    inv = {m: summary.invested(conn, m, owner) for m in data_months}
    return {"spend": spend, "inc": inc, "inc_tot": inc_tot, "exp_tot": {m: round(sum(d.values()), 2) for m, d in spend.items()},
            "inv": inv, "months": data_months}


def _vs(value: float, usual: float | None) -> str:
    if usual is None:
        return '<span class="cap">–</span>'
    d = value - usual
    if abs(d) < max(150, usual * 0.10):
        return '<span class="cap">≈ usual</span>'
    return f'<span class="{"up" if d > 0 else "down"}">{"▲" if d > 0 else "▼"} {money(abs(d))}</span>'


def _cat_options(conn) -> str:
    return "".join(f"<option>{esc(r[0])}</option>" for r in conn.execute(
        "SELECT name FROM categories WHERE name != 'Uncategorised' AND kind IN ('expense','fee','debt','income') ORDER BY kind, name"))


def _change_form(ctx: Ctx, line_id, cats: str, back: str, month: str | None = None) -> str:
    if ctx.readonly:
        return ""
    if not line_id:
        return '<span class="cap">from your old sheet</span>'
    ok = ('<label class="cap"><input type="checkbox" name="ok_closed" value="1" required> this month is closed, change it anyway</label> '
          if month and month in ctx.closed else "")
    return ('<details class="chg"><summary>change category / leave out</summary>'
            + _form(ctx, "/line/category", f'<input type="hidden" name="id" value="{line_id}"><input type="hidden" name="back" value="{esc(back)}">{ok}'
                    f'<select name="category" required><option value="" selected disabled>choose…</option>{cats}</select> '
                    '<label class="cap"><input type="checkbox" name="learn" value="1"> remember</label> <button class="quiet">Move</button>', "row")
            + _form(ctx, "/line/leaveout", f'<input type="hidden" name="id" value="{line_id}"><input type="hidden" name="on" value="1">'
                    f'<input type="hidden" name="back" value="{esc(back)}">{ok}<button class="quiet" title="A one-off, like a car or a trip. It stays in your records but not in the totals.">'
                    'Leave out of the totals</button>', "row")
            + "</details>")


def _tiles(inc: float, exp: float, inv: float, label: str = "") -> str:
    kept = inc - exp
    stay = kept - inv
    sub = (f"{money(inv)} invested · {money(stay)} stayed in the bank" if inv else f"{money(kept)} stayed in the bank")
    return ('<div class="trio">'
            f'<div><small>{label}Income</small><b>{money(inc)}</b></div>'
            f'<div><small>{label}Expenses</small><b>{money(exp)}</b></div>'
            f'<div><small>{label}Kept</small><b class="{"neg" if kept < 0 else ""}">{money(kept)}</b><small class="k">{esc(sub if kept or inv else "")}</small></div></div>')


def _dataline(conn: sqlite3.Connection, today: str) -> str:
    """One line: how far the statements go, and what is due next."""
    bank = conn.execute("SELECT MAX(s.period_end) FROM statements s JOIN accounts a ON a.id = s.account_id WHERE a.kind = 'bank' AND a.active = 1").fetchone()[0]
    card = conn.execute("SELECT MAX(s.billing_date) FROM statements s JOIN accounts a ON a.id = s.account_id WHERE a.kind = 'card' AND a.active = 1").fetchone()[0]
    parts = []
    if bank:
        parts.append(f"bank to <b>{dmy(bank)}</b>")
    if card:
        parts.append(f"cards billed to <b>{dmy(card)}</b>")
    if not parts:
        return ""
    cl = expected.checklist(conn, today)
    soon, late = [], []
    for g in cl["groups"]:
        for i in g["items"]:
            found = re.search(r"around (\d{4}-\d{2}-\d{2})", i.get("detail") or "")
            if not found:
                continue
            when = found.group(1)
            if i["status"] == "waiting":
                soon.append((when, i["label"]))
            elif i["status"] == "todo" and not i.get("children"):
                late.append((when, i["label"]))
    text = "Statements: " + ", ".join(parts) + "."
    if late:
        text += " Overdue: " + ", ".join(f"{esc(l)} ({dmy(w)})" for w, l in sorted(late)[:3]) + "."
    if soon:
        w, l = min(soon)
        text += f" Next: {esc(l)} around {dmy(w)}."
    return f'<p class="dataline">{text}</p>'


def _tasks(conn: sqlite3.Connection, ctx: Ctx) -> str:
    if ctx.readonly:
        return ""
    c = counts(conn)
    cl = expected.checklist(conn, ctx.today)
    missing = sum(1 for g in cl["groups"] for i in g["items"] if i["status"] == "todo" and not i.get("children"))
    pend = [p for p in balances.pending(conn, ctx.today) if not p["current"] and not p["folded"]]
    drafts = conn.execute("SELECT COUNT(*) FROM batches WHERE status != 'committed'").fetchone()[0]
    chips = []
    if missing:
        chips.append(f'<a class="chip" href="/upload"><b>{missing}</b> statement(s) to drop in</a>')
    if c["review"]:
        chips.append(f'<a class="chip" href="/review"><b>{c["review"]}</b> payments to sort</a>')
    if pend:
        chips.append(f'<a class="chip" href="/balances"><b>{len(pend)}</b> balances to update</a>')
    if c["items"]:
        chips.append(f'<a class="chip" href="/items"><b>{c["items"]}</b> to check</a>')
    if drafts:
        chips.append(f'<a class="chip" href="/imports"><b>{drafts}</b> import(s) to submit</a>')
    nxt = closing.to_close(conn, ctx.today)
    if nxt:
        chips.append(f'<a class="chip" href="/close?m={nxt}">Close {month_label(nxt)}</a>')
    body = "".join(chips) if chips else '<span class="cap">Nothing left to do. ✔</span>'
    return f'<div class="card" style="margin-top:14px"><h4 style="margin:0 0 8px">To do</h4><div class="todo">{body}</div></div>'


def _controls(base: str, mode: str, owner: str | None, m: str, end: str, frm: str, to: str, owners: list[str], find: str) -> str:
    own = {"owner": owner}
    seg = " ".join(f'<a class="{"on" if mode == key else ""}" href="{base}{_qs(p=key, end=end, **own)}">{label}</a>'
                   for key, label in (("month", "Month"), ("6", "6 months"), ("12", "12 months")))
    seg += f' <a class="{"on" if mode == "custom" else ""}" href="#custom" onclick="document.getElementById(\'custom\').open=true">Custom</a>'
    who = " ".join(f'<a class="{"on" if (owner or "") == v else ""}" href="{base}{_qs(p=("custom" if mode == "custom" else mode), m=m if mode == "month" else None, end=end if mode != "month" else None, **{"from": frm if mode == "custom" else None, "to": to if mode == "custom" else None}, owner=v)}">{t}</a>'
                   for v, t in [("", "Household")] + [(o, o) for o in owners])
    if mode == "month":
        nav = (f'<a class="nb" href="{base}{_qs(p="month", m=shift(m, -1), **own)}" aria-label="Previous month">‹</a><b class="mt">{month_label(m)}</b>'
               f'<a class="nb" href="{base}{_qs(p="month", m=shift(m, 1), **own)}" aria-label="Next month">›</a>')
    elif mode == "custom":
        nav = f'<b class="mt">{short_label(frm)} to {short_label(to)}</b>'
    else:
        nav = (f'<a class="nb" href="{base}{_qs(p=mode, end=shift(end, -1), **own)}" aria-label="Earlier">‹</a><b class="mt">last {mode} months with data, up to {month_label(end)}</b>'
               f'<a class="nb" href="{base}{_qs(p=mode, end=shift(end, 1), **own)}" aria-label="Later">›</a>')
    custom = (f'<details id="custom" style="margin-bottom:8px"{" open" if mode == "custom" else ""}><summary class="cap">Choose your own period</summary>'
              f'<form method="get" action="{base}" class="row" style="margin-top:6px"><input type="hidden" name="p" value="custom">'
              f'<input type="hidden" name="owner" value="{esc(owner or "")}">'
              f'<label>From <input type="month" name="from" value="{esc(frm)}"></label> <label>to <input type="month" name="to" value="{esc(to)}"></label> '
              '<button>Show</button></form></details>')
    search = (f'<form method="get" action="{base}" class="row" style="margin:0 0 0 auto"><input name="find" value="{esc(find)}" placeholder="Search all payments" '
              'style="width:200px"><button class="quiet">Search</button></form>')
    return (f'<div class="modes"><span class="seg">{seg}</span><span class="monthnav">{nav}</span><span class="seg">{who}</span>{search}</div>{custom}')


def overview_page(conn: sqlite3.Connection, ctx: Ctx, q: dict, base: str = "/") -> str:
    owner = q.get("owner") or None
    find = (q.get("find") or "").strip()
    from . import firstrun
    loaded = firstrun.pending_notice(conn)
    loaded = None if ctx.readonly else loaded
    notice = ('<div class="banner good"><details><summary>Your starting data is loaded</summary><ul>'
              + "".join(f"<li>{esc(x)}</li>" for x in loaded) + "</ul></details>"
              + _form(ctx, "/seed/dismiss", "<button class=quiet>Got it</button>", "inline") + "</div>") if loaded else ""
    from . import sheetsync
    st = sheetsync.status(conn)
    if st["linked"] and st["last_result"] and st["last_result"].startswith("failed"):
        notice += f'<div class="banner">The Google Sheet did not update: {esc(st["last_result"][8:])} <a href="/sheet">Details</a></div>'
    owners = [r[0] for r in conn.execute("SELECT name FROM owners ORDER BY name")]
    d = _load(conn, owner)
    months = d["months"]
    if not months:
        return layout(conn, ctx, "/", "Overview", f'<h1>Overview</h1>{notice}<div class="card"><p>No spending or income yet. '
                      'Drop your statements on the <a href="/upload">Upload</a> page.</p></div>' + _tasks(conn, ctx))
    this_month = ctx.today[:7]
    complete = [m for m in months if m < this_month]
    latest = (complete or months)[-1]
    mode = q.get("p") if q.get("p") in ("month", "6", "12", "custom") else "month"
    m = q.get("m") if q.get("m") and len(q.get("m", "")) == 7 else latest
    end = q.get("end") if q.get("end") and len(q.get("end", "")) == 7 else latest
    frm = q.get("from") if q.get("from") and len(q.get("from", "")) == 7 else shift(latest, -5)
    to = q.get("to") if q.get("to") and len(q.get("to", "")) == 7 else latest
    if frm > to:
        frm, to = to, frm
    controls = _controls(base, mode, owner, m, end, frm, to, owners, find)
    cats = _cat_options(conn)
    back = base + _qs(**{k: v for k, v in q.items() if k in ("p", "m", "end", "from", "to", "owner", "open")})
    vit = _vituri_note(conn, ctx, base, d, mode, m, end, frm, to) + _oneoff_note(conn, ctx, d, mode, m, end, frm, to, owner, back)

    if find:
        rows = summary.find_lines(conn, find)
        body = (f'<div class="card"><h2>Payments matching “{esc(find)}” ({len(rows)})</h2><div style="overflow-x:auto"><table class="ct"><thead><tr><th>Date</th><th>Description</th>'
                f'<th>Account</th><th>Category</th><th class="n">Amount ₪</th><th></th></tr></thead><tbody>'
                + "".join(f'<tr><td>{esc(r["txn_date"])}</td><td dir="auto">{esc(r["description"])}</td><td dir="auto" class="muted">{esc(r["account"])}</td>'
                          f'<td>{esc(r["category"])}{" ?" if r["category_status"] == "proposed" else ""}</td><td class="n">{r["spent"]:,.2f}</td>'
                          f'<td>{_change_form(ctx, r["id"], cats, back, r["budget_month"])}</td></tr>' for r in rows)
                + '</tbody></table></div><p class="cap">Amounts are money out; refunds and income show as negative. A “?” means only proposed so far.</p></div>')
        return layout(conn, ctx, "/", "Overview", f"<h1>Overview</h1>{notice}{controls}{body}")

    if mode == "month":
        body = _month_view(conn, ctx, d, m, owner, cats, back, base, q)
    else:
        win = months_between(frm, to) if mode == "custom" else [x for x in months if x <= end][-int(mode):]
        body = _period_view(conn, ctx, d, win, owner, base, mode, q)
    return layout(conn, ctx, "/", "Overview", f"<h1>Overview</h1>{notice}{controls}{_dataline(conn, ctx.today)}{body}{vit}{_tasks(conn, ctx)}")


def _vituri_note(conn, ctx, base, d, mode, m, end, frm, to) -> str:
    if ctx.readonly:
        return ""
    inc_r = summary.include_reimbursed(conn)
    win = [m] if mode == "month" else (months_between(frm, to) if mode == "custom" else [x for x in d["months"] if x <= end][-int(mode):])
    amount = sum(summary.reimbursed_total(conn, x) for x in win)
    if not amount and not inc_r:
        return ""
    return ('<p class="cap" style="margin-top:12px">'
            + _form(ctx, "/setting/reimbursed", f'<span>Vituri (paid back in cash), {money(amount)} here, is {"counted in" if inc_r else "left out of"} these numbers.</span> '
                    f'<input type="hidden" name="on" value="{0 if inc_r else 1}"><input type="hidden" name="next" value="/">'
                    f'<button class="quiet">{"Leave out" if inc_r else "Include"}</button>', "row") + "</p>")


def _oneoff_note(conn, ctx, d, mode, m, end, frm, to, owner, back="/") -> str:
    win = [m] if mode == "month" else (months_between(frm, to) if mode == "custom" else [x for x in d["months"] if x <= end][-int(mode):])
    out = summary.oneoff_left_out(conn, win, owner)
    if not out:
        return ""
    def row(o):
        undo = "" if ctx.readonly else _form(ctx, "/line/leaveout", f'<input type="hidden" name="id" value="{o["id"]}"><input type="hidden" name="on" value="0">'
                                              f'<input type="hidden" name="back" value="{esc(back)}"><button class="quiet">Count it again</button>', "inline")
        return (f'<div class="oddrow"><span>{esc(o["txn_date"][8:10])}/{esc(o["txn_date"][5:7])}</span><span><bdi dir="auto">{esc(o["description"])}</bdi>'
                f'<span class="cap"> · {esc(o["category"])}</span></span><span class="n">{money(o["spent"], "ILS", 0)}</span><span>{undo}</span></div>')
    return (f'<div class="adjust"><b>Left out of the numbers</b> <span class="cap">{len(out)} payment(s), {money(sum(o["spent"] for o in out))}. '
            f'You chose these as one-offs.</span>{"".join(row(o) for o in out)}</div>')


def _pace(conn, ctx, d, m, owner) -> str:
    """The month added up day by day against the usual month."""
    import calendar
    from statistics import median
    y, mo = int(m[:4]), int(m[5:7])
    n = calendar.monthrange(y, mo)[1]

    def cum(month):
        days = summary.daily_spending(conn, month, owner)
        if not days:
            return None
        out, run = [], 0.0
        for day in range(1, calendar.monthrange(int(month[:4]), int(month[5:7]))[1] + 1):
            run += days.get(day, 0.0)
            out.append(run)
        return (out + [out[-1]] * 31)[:n]

    prior = [x for x in d["months"] if x < m][-USUAL_MONTHS:]
    earlier = [c for c in (cum(x) for x in prior) if c]
    mine = cum(m)
    if len(earlier) < 2 or not mine:
        return ""
    usual = [median(c[i] for c in earlier) for i in range(n)]
    low = [min(c[i] for c in earlier) for i in range(n)]
    high = [max(c[i] for c in earlier) for i in range(n)]
    cutoff = n
    if m == ctx.today[:7]:
        cutoff = int(ctx.today[8:10])
    elif m > ctx.today[:7]:
        return ""
    this = [mine[i] if i < cutoff else None for i in range(n)]
    ahead = mine[cutoff - 1] - usual[cutoff - 1]
    verdict = (f"Expenses so far are {money(abs(ahead))} {'above' if ahead > 0 else 'below'} what you usually have spent by this day of the month"
               if abs(ahead) >= 1 else "Expenses so far are right on what you usually have spent by this day")
    return ('<div class="card" style="margin-top:14px"><h4 style="margin:0 0 4px">Expenses, day by day</h4>'
            f'<p class="cap" style="margin:0 0 8px">{verdict}. This is only expenses, not income. Each step up is money leaving your accounts: '
            'card spending counts on the day the card is billed, everything else on the day it was paid. '
            f'The line ends at the Expenses total above. The dashed line is your usual month and the grey band is the range of your last {len(earlier)} months.</p>'
            + charts.pace_chart(this, usual, low, high, "ILS", "Expenses added up through the month", m) + "</div>")


def _budget_month(conn, ctx, spend, owner) -> str:
    bs = budgets.get_all(conn)
    if owner:
        return ""
    if not bs:
        return "" if ctx.readonly else '<p class="cap" style="margin-top:10px"><a href="/budgets">Set monthly budgets</a> to see how much of each is used.</p>'
    return budget_card(ctx, bs, spend, "Against your budget")


def _month_view(conn, ctx, d, m, owner, cats, back, base, q) -> str:
    spend = d["spend"].get(m, {})
    exp, inc, inv = d["exp_tot"].get(m, 0.0), d["inc_tot"].get(m, 0.0), d["inv"].get(m, 0.0)
    prior = [x for x in d["spend"] if x < m][-USUAL_MONTHS:]
    usual = {c: sum(d["spend"][x].get(c, 0) for x in prior) / len(prior) for c in {c for x in prior for c in d["spend"][x]}} if prior else {}
    usual_total = sum(d["exp_tot"][x] for x in prior) / len(prior) if prior else None
    if not spend and not inc:
        return _tiles(0, 0, 0) + '<div class="card" style="margin-top:14px"><p>Nothing recorded for this month yet.</p></div>'
    note = ""
    if m in ctx.closed:
        note = ('<p class="cap"><span class="chip good"><b>✔</b> Closed</span> You closed this month. '
                'Changing a payment still works, but the numbers move.' + ("" if ctx.readonly else f' <a href="/close?m={m}">Reopen</a>') + '</p>')
    if m >= ctx.today[:7]:
        note += '<p class="cap">This month isn’t over yet, so the numbers are partial.</p>'
    open_cat = q.get("open")
    rows = []
    ranked = sorted(spend.items(), key=lambda kv: -kv[1])
    for cat, v in ranked:
        lines = summary.category_lines(conn, cat, [m], owner)
        line_rows = "".join(
            f'<div><span>{esc(l["txn_date"][8:10])}/{esc(l["txn_date"][5:7])}</span><span><bdi dir="auto">{esc(l["description"])}</bdi>'
            f'<span class="cap"> · {esc(l["account"])}{"" if l["subcategory"] == cat else " · " + esc(l["subcategory"])}{" · ?" if l["category_status"] == "proposed" else ""}</span></span>'
            f'<span class="n">{money(l["spent"], "ILS", 0)}</span><span>{_change_form(ctx, l["id"], cats, back, l["budget_month"])}</span></div>' for l in lines)
        rows.append(f'<details class="cat"{" open" if open_cat == cat else ""}><summary><span class="tw">›</span><span dir="auto">{esc(cat)}</span>'
                    f'<span class="share hidem"><span class="t"><span style="width:{v / ranked[0][1] * 100:.0f}%"></span></span><em>{v / (exp or 1) * 100:.0f}%</em></span>'
                    f'<span class="n">{money(v)}</span><span class="n">{_vs(v, usual.get(cat, 0.0) if prior else None)}</span></summary>'
                    f'<div class="lines">{line_rows or "<div><span></span><span class=cap>No lines to show.</span><span></span><span></span></div>"}</div></details>')
    head = ('<div class="cat head"><span></span><span>Category</span><span class="hidem">Share of expenses</span><span class="n">Spent</span>'
            f'<span class="n">vs. your {len(prior)}-month average</span></div>') if prior else ""
    total_vs = _vs(exp, usual_total)
    foot = f'<div class="cat foot"><span></span><span>Total</span><span class="hidem"></span><span class="n">{money(exp)}</span><span class="n">{total_vs}</span></div>'
    inc_rows = "".join(f'<div class="srcrow"><span dir="auto">{esc(k)}</span><span class="n">{money(v)}</span></div>'
                       for k, v in sorted(d["inc"].get(m, {}).items(), key=lambda kv: -kv[1]))
    income = (f'<details class="card" style="margin-top:14px"><summary><b>Where the income came from</b> <span class="cap">{money(inc)}</span></summary>{inc_rows}</details>'
              if inc_rows else "")
    fixed = insights.fixed_costs(conn, m, d["months"], owner)
    odd = insights.unusual(conn, m, d["months"], owner, fixed)
    return (f'{note}{_tiles(inc, exp, inv)}{_pace(conn, ctx, d, m, owner)}<h3 style="margin:18px 0 6px;font-size:16px">Where it went</h3>'
            f'<div class="cats">{head}{"".join(rows)}{foot}</div>{_budget_month(conn, ctx, spend, owner)}{_worth_a_look(ctx, odd, cats, back, m)}{_fixed_card(fixed, m)}{income}')


def _worth_a_look(ctx, odd, cats, back, month=None) -> str:
    if not odd:
        return ""
    rows = "".join(
        f'<div class="oddrow"><span>{esc(o["date"][8:10])}/{esc(o["date"][5:7])}</span><span><bdi dir="auto">{esc(o["description"])}</bdi>'
        f'<span class="cap"> · {esc(o["category"])} · {esc(o["why"])}</span></span><span class="n">{money(o["amount"], "ILS", 0)}</span>'
        f'<span>{_change_form(ctx, o["id"], cats, back, month)}</span></div>' for o in odd)
    return (f'<div class="card" style="margin-top:14px"><h4 style="margin:0 0 8px">Worth a look <span class="cap">payments that stand out this month</span></h4>{rows}</div>')


def _fixed_card(fixed, m) -> str:
    if not fixed:
        return ""
    tag = {"ok": '<span class="cap">✔ paid</span>', "changed": '<span class="up">▲ changed</span>', "not yet": '<span class="cap">not yet</span>',
           "missing": '<span class="up">▲ not seen</span>'}
    rows = "".join(
        f'<div class="oddrow"><span></span><span><bdi dir="auto">{esc(f["name"])}</bdi><span class="cap"> · {esc(f["category"])} · seen in {f["months"]} of the last 6 months</span></span>'
        f'<span class="n">{money(f["this"], "ILS", 0) if f["this"] is not None else "–"}'
        f'<span class="cap"> usual {money(f["usual"], "ILS", 0)}</span></span><span>{tag[f["status"]]}</span></div>' for f in fixed)
    return (f'<div class="card" style="margin-top:14px"><h4 style="margin:0 0 8px">Fixed costs <span class="cap">repeat every month at about the same amount</span></h4>{rows}</div>')


def _period_stacks(conn, d, have, owner) -> str:
    """Two charts under the income/expense bars: spending split by category, and by fixed or flexible."""
    if len(have) < 2:
        return ""
    totals: dict[str, float] = {}
    for x in have:
        for c, v in d["spend"].get(x, {}).items():
            totals[c] = totals.get(c, 0) + v
    top = sorted(totals, key=lambda c: -totals[c])[:6]
    series = [(c, [d["spend"].get(x, {}).get(c, 0.0) for x in have]) for c in top]
    rest = [round(sum(v for c, v in d["spend"].get(x, {}).items() if c not in top), 2) for x in have]
    if any(rest):
        series.append((charts.OTHER, rest))
    labels = [short_label(x) for x in have]
    cat_html = charts.stacked_bars(labels, series, "ILS", "Spending by category, month by month")
    fixed = []
    for x in have:
        f = insights.fixed_costs(conn, x, d["months"], owner)
        fixed.append(min(round(sum(i["this"] or 0 for i in f), 2), d["exp_tot"].get(x, 0.0)))
    flex = [round(max(d["exp_tot"].get(x, 0.0) - f, 0.0), 2) for x, f in zip(have, fixed)]
    fix_html = charts.stacked_bars(labels, [("Fixed (mortgage, loans, regular bills)", fixed), ("Everything else", flex)], "ILS", "Fixed and flexible spending")
    share = sum(flex) / (sum(flex) + sum(fixed) or 1) * 100
    return (f'<h4 style="margin:18px 0 4px">Where it went, month by month</h4>{cat_html}'
            + charts.data_table(["Month", *[n for n, _ in series]], [[labels[i], *[f"{v[i]:,.0f}" for _, v in series]] for i in range(len(have))], "Spending by category and month")
            + f'<h4 style="margin:18px 0 4px">What you can actually change</h4><p class="cap" style="margin:0 0 6px">About {share:.0f}% of your spending is flexible. '
              f'Fixed costs are the ones that repeat every month at about the same amount.</p>{fix_html}')


def _budget_period(conn, ctx, cat_tot, n, owner) -> str:
    bs = budgets.get_all(conn)
    if owner or not bs:
        return ""
    return budget_card(ctx, bs, {c: v / n for c, v in cat_tot.items()}, "Average per month against your budget")


def _period_view(conn, ctx, d, win, owner, base, mode, q) -> str:
    have = [x for x in win if x in d["months"]]
    if not have:
        return '<div class="card"><p>No data in this period.</p></div>'
    n = len(have)
    avg_inc = sum(d["inc_tot"].get(x, 0) for x in have) / n
    avg_exp = sum(d["exp_tot"].get(x, 0) for x in have) / n
    avg_inv = sum(d["inv"].get(x, 0) for x in have) / n
    skipped = [x for x in months_between(win[0], win[-1]) if x not in d["months"]] if win else []
    note = (f'<p class="cap" style="margin:4px 0 10px">Based on the {n} month(s) with data: {", ".join(short_label(x) for x in have)}. '
            + (f'{", ".join(short_label(x) for x in skipped)} {"has" if len(skipped) == 1 else "have"} no data, so '
               f'{"it is" if len(skipped) == 1 else "they are"} left out instead of counted as zero.' if skipped else "") + "</p>")
    chart = (charts.grouped_bars(have, [d["inc_tot"].get(x) for x in have], [d["exp_tot"].get(x) for x in have])
             + charts.data_table(["Month", "Income", "Expenses", "Kept"], [[short_label(x), f'{d["inc_tot"].get(x, 0):,.0f}', f'{d["exp_tot"].get(x, 0):,.0f}',
                                                                       f'{d["inc_tot"].get(x, 0) - d["exp_tot"].get(x, 0):,.0f}'] for x in have], "Income and expenses by month"))
    chart += _period_stacks(conn, d, have, owner)
    cat_tot: dict[str, float] = {}
    cat_hi: dict[str, tuple[str, float]] = {}
    for x in have:
        for c, v in d["spend"].get(x, {}).items():
            cat_tot[c] = cat_tot.get(c, 0) + v
            if v > cat_hi.get(c, ("", 0))[1]:
                cat_hi[c] = (x, v)
    ranked = sorted(cat_tot, key=lambda c: -cat_tot[c])
    top = cat_tot[ranked[0]] / n if ranked else 1
    rows = []
    for c in ranked:
        per = "".join(f'<div><span>{esc(month_label(x))}</span><span></span><span class="n">{money(d["spend"].get(x, {}).get(c, 0))}</span>'
                      f'<span><a href="{base}{_qs(p="month", m=x, open=c, owner=owner)}">see the payments</a></span></div>' for x in have)
        hi = cat_hi.get(c)
        rows.append(f'<details class="cat"><summary><span class="tw">›</span><span dir="auto">{esc(c)}</span>'
                    f'<span class="share hidem"><span class="t"><span style="width:{cat_tot[c] / n / top * 100:.0f}%"></span></span><em>{cat_tot[c] / (sum(cat_tot.values()) or 1) * 100:.0f}%</em></span>'
                    f'<span class="n"><b>{money(cat_tot[c] / n)}</b></span><span class="n cap hidem">{esc(short_label(hi[0]) + " · " + money(hi[1])) if hi else ""}</span></summary>'
                    f'<div class="lines">{per}</div></details>')
    head = (f'<div class="cat head"><span></span><span>Category</span><span class="hidem">Share</span><span class="n">Average / month</span>'
            f'<span class="n hidem">Highest month</span></div>')
    foot = f'<div class="cat foot"><span></span><span>Total</span><span class="hidem"></span><span class="n">{money(sum(cat_tot.values()) / n)}</span><span class="n cap hidem">{money(sum(cat_tot.values()))} over {n} months</span></div>'
    return (_tiles(avg_inc, avg_exp, avg_inv, "Average ").replace("Average Income", "Average income / month").replace("Average Expenses", "Average expenses / month").replace("Average Kept", "Average kept / month")
            + note + f'<div class="card" style="margin-top:6px">{chart}</div>{_budget_period(conn, ctx, cat_tot, n, owner)}<h3 style="margin:18px 0 6px;font-size:16px">Average per category</h3>'
            f'<div class="cats">{head}{"".join(rows)}{foot}</div>')
