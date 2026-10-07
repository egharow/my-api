"""HTML pages. Every function takes an open connection and returns a string, so they are easy to test."""
import sqlite3
from dataclasses import dataclass, field
from html import escape as esc

from datetime import date

from . import balances, charts, commits, discrepancies, expected, fx, goals, reconcile, sheetsync, summary
from . import BUILD
from .charts import money


@dataclass
class Ctx:
    today: str
    who: str = ""
    csrf: str = ""
    flash: str = ""
    flash_kind: str = "ok"
    owners: list[str] = field(default_factory=list)
    inbox_count: int = 0
    data_root: str = ""
    starter: bool = False
    can_shortcut: bool = False


CSS = """
:root{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#6b6a65;--grid:#e1e0d9;--axis:#c3c2b7;
--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--good:#0ca30c;--goodtext:#006300;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;--accent:#2a78d6}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#a3a198;--grid:#2c2c2a;--axis:#383835;
--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--goodtext:#0ca30c;--accent:#3987e5}}
:root[data-theme="dark"]{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#a3a198;--grid:#2c2c2a;--axis:#383835;
--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--goodtext:#0ca30c;--accent:#3987e5}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--accent)}header{background:var(--surface);border-bottom:1px solid var(--border);padding:0 16px}
nav{display:flex;gap:4px;flex-wrap:wrap;align-items:center;max-width:1100px;margin:0 auto}
nav a{padding:12px 10px;color:var(--ink2);text-decoration:none;border-bottom:2px solid transparent}
nav a.on{color:var(--ink);border-color:var(--accent)}nav .sp{flex:1}
nav .badge{background:var(--s2);color:#fff;border-radius:9px;padding:0 6px;font-size:12px;margin-left:4px}
main{max-width:1100px;margin:0 auto;padding:16px}h1{font-size:22px;margin:8px 0 12px}h2{font-size:16px;margin:0 0 8px}
.grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;min-width:0}
.tiles{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-bottom:16px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.tile .v{font-size:26px;font-weight:600}.tile .l{color:var(--ink2);font-size:13px}.tile .d{font-size:13px;color:var(--muted)}
.muted{color:var(--muted)}small{color:var(--muted)}.chart{width:100%;height:auto}.chart .grid{stroke:var(--grid);stroke-width:1}
.chart .baseline{stroke:var(--axis)}.chart .axis{fill:var(--muted);font-size:11px}.chart .direct{fill:var(--ink);font-size:12px;font-weight:600}
.legend{display:flex;gap:14px;font-size:13px;color:var(--ink2);margin-bottom:6px}.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.hrow{display:grid;grid-template-columns:minmax(90px,150px) 1fr 110px;gap:8px;align-items:center;padding:3px 0;font-size:14px}
.htrack{background:var(--grid);border-radius:4px;height:10px;overflow:hidden}.hbar{display:block;height:10px;background:var(--s1);border-radius:4px}
.hval{text-align:right}.hval small{margin-left:6px}.hlabel{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pbar{background:var(--grid);border-radius:5px;height:10px;overflow:hidden;margin:6px 0}.pfill{display:block;height:10px;background:var(--s1)}
.pfill.on_track,.pfill.reached{background:var(--good)}.pfill.behind{background:var(--serious)}
.chip{display:inline-block;border:1px solid var(--border);border-radius:12px;padding:0 9px;font-size:13px}
.chip.good b{color:var(--goodtext)}.chip.serious b{color:var(--serious)}.chip.warning b{color:var(--warning)}.chip.critical b{color:var(--critical)}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}details.tableview{margin-top:8px;font-size:13px}details summary{cursor:pointer;color:var(--ink2)}
form.inline{display:inline}input,select,textarea,button{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--axis);border-radius:6px;padding:5px 8px}
button{cursor:pointer;background:var(--accent);color:#fff;border-color:var(--accent)}button.quiet{background:transparent;color:var(--ink);border-color:var(--axis)}
.flash{padding:10px 14px;border-radius:8px;margin-bottom:12px;border:1px solid var(--border);background:var(--surface)}.flash.err{border-color:var(--critical)}
.banner{border-left:4px solid var(--warning);background:var(--surface);padding:10px 14px;border-radius:6px;margin-bottom:12px}
.item{padding:10px 0;border-bottom:1px solid var(--grid)}.sev{font-weight:600}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.comment{border-left:3px solid var(--axis);padding:2px 10px;margin:8px 0}.comment b{font-size:13px}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--surface);padding:5px 9px;border-radius:6px;font-size:13px;display:none;z-index:9;max-width:320px}
@media (max-width:600px){.hrow{grid-template-columns:90px 1fr 88px}.tile .v{font-size:22px}}
"""

HEARTBEAT = """
(function(){function p(){fetch('/ping',{cache:'no-store'}).catch(function(){})}p();setInterval(p,15000)})();
"""

JS = """
(function(){var t=document.getElementById('tip');
document.addEventListener('mouseover',function(e){var el=e.target.closest('[data-tip]');if(!el){t.style.display='none';return}
t.textContent=el.getAttribute('data-tip');t.style.display='block'});
document.addEventListener('mousemove',function(e){t.style.left=Math.min(e.clientX+14,innerWidth-t.offsetWidth-8)+'px';t.style.top=(e.clientY+16)+'px'});
document.addEventListener('mouseout',function(e){if(!e.relatedTarget||!e.relatedTarget.closest||!e.relatedTarget.closest('[data-tip]'))t.style.display='none'});})();
"""

NAV = [("/", "Home"), ("/upload", "Upload"), ("/review", "Review"), ("/spending", "Spending"), ("/wealth", "Wealth"), ("/setup", "Settings")]


def _form(ctx: Ctx, action: str, inner: str, cls: str = "") -> str:
    return (f'<form method="post" action="{esc(action)}" class="{cls}"><input type="hidden" name="csrf" value="{esc(ctx.csrf)}">'
            f"{inner}</form>")


def counts(conn: sqlite3.Connection) -> dict:
    return {
        "review": conn.execute("""SELECT COUNT(DISTINCT t.description_norm) FROM transactions t JOIN batches b ON b.id = t.batch_id
                                  WHERE t.category_status = 'proposed' AND b.status != 'committed'""").fetchone()[0],
        "items": conn.execute("SELECT COUNT(*) FROM discrepancies WHERE status = 'open' AND type != 'manual'").fetchone()[0],
    }


def layout(conn, ctx: Ctx, path: str, title: str, body: str) -> str:
    c = counts(conn)
    links = []
    for href, name in NAV:
        badge = ""
        if href == "/review" and c["review"]:
            badge = f'<span class="badge">{c["review"]}</span>'
        if href == "/items" and c["items"]:
            badge = f'<span class="badge">{c["items"]}</span>'
        links.append(f'<a href="{href}" class="{"on" if path == href else ""}">{name}{badge}</a>')
    who = "".join(f'<option value="{esc(o)}"{" selected" if o == ctx.who else ""}>{esc(o)}</option>' for o in ctx.owners)
    flash = f'<div class="flash {"err" if ctx.flash_kind == "err" else ""}" role="status">{esc(ctx.flash)}</div>' if ctx.flash else ""
    who_form = (_form(ctx, "/who", "<label class=muted>You are <select name=who onchange=this.form.submit()>" + who + "</select></label>", "inline")
                if ctx.owners else "")
    quit_form = _form(ctx, "/quit", "<button class=quiet title='Close the app'>Quit</button>", "inline")
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{esc(title)} · Finance</title><style>{CSS}</style></head><body><header><nav>" + "".join(links)
            + "<span class=\"sp\"></span>" + who_form + quit_form
            + f"</nav></header><main>{flash}{body}</main><p class=\"muted\" style=\"text-align:center;font-size:12px\">Version {esc(BUILD)}</p><div id=\"tip\"></div><script>{JS}{HEARTBEAT}</script></body></html>")


def _tile(label: str, value: str, detail: str = "") -> str:
    return f'<div class="tile"><div class="l">{esc(label)}</div><div class="v">{value}</div><div class="d">{detail}</div></div>'


def _net_worth_series(conn, currency: str):
    """Every snapshot valued at today's dollar rate. Returns (points, no_rate)."""
    pts = []
    for d in [r[0] for r in conn.execute("SELECT DISTINCT as_of FROM balances ORDER BY as_of")]:
        try:
            pts.append((d, balances.net_worth(conn, d, currency)["total"]))
        except LookupError:
            return [], True
    return pts, False


def fx_note(conn, today: str) -> str:
    cur = fx.current_rate(conn)
    if not cur:
        return "no dollar rate yet"
    age = (date.fromisoformat(today) - date.fromisoformat(cur["date"])).days
    stale = f" ▲ {age} days old" if age > 3 else ""
    return f"₪{cur['rate']:.3f} per $ · {cur['source']} · {cur['date']}{stale}"


def dashboard(conn: sqlite3.Connection, ctx: Ctx, q: dict) -> str:
    cur = q.get("cur", "ILS") if q.get("cur") in ("ILS", "USD") else "ILS"
    owner = q.get("owner") or None
    months_all = summary.months_available(conn)
    overview = [summary.month_overview(conn, m) for m in months_all[-18:]]
    with_data = [o for o in overview if o["spending"] or o["income"]]
    window = _month_window([o["month"] for o in with_data], 18)
    by_month = {o["month"]: o for o in with_data}
    chosen = q.get("month") or (with_data[-1]["month"] if with_data else None)
    from .views_setup import dropzone
    out = ['<h1>Dashboard</h1>', dropzone(ctx, compact=True)]

    from . import firstrun
    loaded = firstrun.pending_notice(conn)
    if loaded:
        out.append('<div class="banner" style="border-color:var(--good)"><b>Your starting data is loaded.</b><ul>' + "".join(f"<li>{esc(x)}</li>" for x in loaded)
                   + "</ul>Everything is a draft: check the <a href='/review'>Review</a> tab, then submit on the <a href='/imports'>Imports</a> tab. "
                   + _form(ctx, "/seed/dismiss", "<button class=quiet>Got it</button>", "inline") + "</div>")
    drafts = conn.execute("SELECT COUNT(*) FROM batches WHERE status != 'committed'").fetchone()[0]
    if drafts:
        out.append(f'<div class="banner">{drafts} import(s) are drafts. Numbers below include them until you submit. <a href="/imports">Review and submit</a></div>')
    st = sheetsync.status(conn)
    if not st["linked"]:
        out.append('<div class="banner">The Google Sheet is not linked yet. Set it up once and it updates itself after every submit. '
                   '<a href="/sheet">Link it</a></div>')
    elif st["last_result"] and st["last_result"].startswith("failed"):
        out.append(f'<div class="banner">The Google Sheet did not update: {esc(st["last_result"][8:])} <a href="/sheet">Details</a></div>')
    items = expected.expected_files(conn, ctx.today)
    late = [i for i in items if i["status"] in ("missing", "due")]
    if late:
        rows = "".join(f'<li>{esc(i["source"])} {esc(i["period"])}: {esc(i["detail"])} ({esc(i["status"])})</li>' for i in late[:6])
        more = f" and {len(late) - 6} more" if len(late) > 6 else ""
        out.append(f'<div class="banner"><b>Files you should have uploaded</b><ul>{rows}</ul>{more}</div>')

    pts, no_rate = _net_worth_series(conn, cur)
    nw_now = pts[-1][1] if pts else None
    nw_prev = pts[-2][1] if len(pts) > 1 else None
    change = f"{money(nw_now - nw_prev, cur)} since {pts[-2][0]}" if nw_prev is not None else ""
    if no_rate:
        out.append('<div class="banner"><b>No dollar rate yet.</b> Your dollar accounts need today\'s rate to be valued. '
                   '<a href="/balances">Enter it or fetch it on the Balances page</a>.</div>')
    ov = next((o for o in overview if o["month"] == chosen), None)
    gp = goals.all_progress(conn, ctx.today)
    on = sum(1 for g in gp if g["status"] in ("on_track", "reached"))
    out.append('<div class="tiles">'
               + _tile("Net worth", money(nw_now, cur), esc(change) or "from your balances")
               + _tile(f"Income {chosen or ''}", money(ov["income"] if ov else None), "")
               + _tile(f"Spending {chosen or ''}", money(ov["spending"] if ov else None), "")
               + _tile("Left over", money(ov["saved"] if ov else None), "")
               + _tile("Goals on track", f"{on} of {len(gp)}" if gp else "–", '<a href="/goals">Goals</a>') + "</div>")

    sw = "".join(f'<a href="/?cur={c}{"&month=" + chosen if chosen else ""}">{c}</a> ' for c in ("ILS", "USD"))
    nw_table = charts.data_table(["Date", f"Net worth {cur}"], [[d, f"{v:,.0f}"] for d, v in pts], "Net worth by snapshot date")
    rate_note = f'<p class="muted">Dollar accounts valued at {esc(fx_note(conn, ctx.today))} on every date.</p>' if pts else ""
    out.append(f'<div class="grid"><section class="card"><h2>Net worth over time <small>{sw}</small></h2>'
               f'{charts.line_chart(pts, cur, "Net worth over time")}{rate_note}{nw_table}</section>')

    mo = window
    inc = [by_month[m]["income"] if m in by_month else None for m in window]
    spent = [by_month[m]["spending"] if m in by_month else None for m in window]
    mtable = charts.data_table(["Month", "Income", "Spending", "Left over"],
                               [[o["month"], f'{o["income"]:,.0f}', f'{o["spending"]:,.0f}', f'{o["saved"]:,.0f}'] for o in with_data],
                               "Income and spending by month")
    out.append(f'<section class="card"><h2>Income and spending</h2>{charts.grouped_bars(mo, inc, spent)}{mtable}</section></div>')

    owners = [r[0] for r in conn.execute("SELECT name FROM owners ORDER BY name")]
    month_opts = "".join(f'<option value="{o["month"]}"{" selected" if o["month"] == chosen else ""}>{o["month"]}</option>'
                         for o in reversed(with_data))
    owner_opts = '<option value="">Household</option>' + "".join(
        f'<option{" selected" if o == owner else ""}>{esc(o)}</option>' for o in owners)
    cats = summary.spending_by_category(conn, chosen, owner) if chosen else []
    grouped: dict[str, float] = {}
    for r in cats:
        grouped[r["category"]] = grouped.get(r["category"], 0) + r["spent"]
    rows = sorted(grouped.items(), key=lambda kv: -kv[1])
    detail = {k: ", ".join(f'{r["subcategory"]} {r["spent"]:,.0f}' for r in cats if r["category"] == k) for k in grouped}
    bars = charts.hbars([(k, v, f"{k}: {detail[k]}") for k, v in rows[:14]])
    reimb = summary.reimbursed_total(conn, chosen, owner) if chosen else 0
    inc_r = summary.include_reimbursed(conn)
    reimb_html = ""
    if reimb or inc_r:
        reimb_html = _form(ctx, "/setting/reimbursed",
                           f'<span>Vituri (paid back in cash): {esc(money(reimb))} this month, '
                           f'{"counted in" if inc_r else "not counted in"} spending.</span>'
                           f'<input type="hidden" name="on" value="{0 if inc_r else 1}">'
                           f'<button>{"Leave out" if inc_r else "Include"}</button>', "row")
    ctable = charts.data_table(["Category", "Spent"], [[k, f"{v:,.2f}"] for k, v in rows], f"Spending by category, {chosen}")
    out.append(f'<section class="card" style="margin-top:16px"><h2>Where the money went</h2>'
               f'<form method="get" class="row"><select name="month" onchange="this.form.submit()">{month_opts}</select>'
               f'<select name="owner" onchange="this.form.submit()">{owner_opts}</select><input type="hidden" name="cur" value="{cur}"></form>'
               f"{reimb_html}{bars}{ctable}</section>")

    if gp:
        cards = "".join(_goal_card(g) for g in gp[:4])
        out.append(f'<section class="card" style="margin-top:16px"><h2>Goals</h2>{cards}</section>')
    return layout(conn, ctx, "/", "Dashboard", "".join(out))


def _month_window(months: list[str], size: int) -> list[str]:
    """Every calendar month from the first to the last with data (at most `size`), so gaps show as gaps."""
    if not months:
        return []
    y, m = map(int, months[-1].split("-"))
    out = []
    for _ in range(size):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    out.reverse()
    first = months[0]
    return [x for x in out if x >= first]


def _goal_card(p: dict) -> str:
    cur = p["currency"]
    pct = f"{p['percent'] * 100:.0f}%" if p["percent"] is not None else "–"
    lines = [f'{esc(money(p["current"], cur))} of {esc(money(p["target"], cur))} ({pct})']
    if p["target_date"]:
        need = money(p["required_per_month"], cur)
        short = f", short by {money(p['gap_per_month'], cur)} a month" if p["gap_per_month"] else ""
        lines.append(f"By {esc(p['target_date'])}: needs {esc(need)} a month{esc(short)}")
    if p["growth_per_month"] is not None:
        lines.append(f"Growing about {esc(money(p['growth_per_month'], cur))} a month lately")
    if p["projected_date"]:
        lines.append(f"At this pace: about {esc(p['projected_date'][:7])}")
    if p.get("note"):
        lines.append(esc(p["note"]))
    return (f'<div class="item"><div class="row"><b>{esc(p["name"])}</b>{charts.status_chip(p["status"])}</div>'
            f'{charts.progress_bar(p["percent"], p["status"])}<small>{"<br>".join(lines)}</small></div>')


def review(conn: sqlite3.Connection, ctx: Ctx, q: dict) -> str:
    groups = conn.execute(
        """SELECT t.description_norm AS key, MIN(t.description) AS description, COUNT(*) AS n,
                  ROUND(SUM(-t.amount), 2) AS total, c.name AS proposed, MIN(t.proposal_basis) AS basis
           FROM transactions t JOIN categories c ON c.id = t.category_id JOIN batches b ON b.id = t.batch_id
           WHERE t.category_status = 'proposed' AND b.status != 'committed'
           GROUP BY t.description_norm, c.name ORDER BY ABS(SUM(t.amount)) DESC LIMIT 60""").fetchall()
    cats = conn.execute("SELECT name FROM categories WHERE name != 'Uncategorised' ORDER BY kind, name").fetchall()
    out = ["<h1>Review categories</h1>",
           '<p class="muted">Each line is a merchant. The proposed category is a suggestion: approve it or pick another. '
           "Tick “remember” and the app will use your choice for this merchant from now on.</p>"]
    if not groups:
        out.append('<div class="card">Nothing is waiting for approval.</div>')
    rows = []
    for g in groups:
        opts = "".join(f'<option{" selected" if c[0] == g["proposed"] else ""}>{esc(c[0])}</option>' for c in cats)
        if g["proposed"] == "Uncategorised":
            opts = "<option value=\"\" selected disabled>choose…</option>" + opts
        form = _form(ctx, "/approve",
                     f'<input type="hidden" name="key" value="{esc(g["key"])}"><select name="category" required>{opts}</select> '
                     f'<label><input type="checkbox" name="learn" value="1" checked> remember</label> <button>Approve</button>', "row")
        flow = f'{money(abs(g["total"]), "ILS", 2)} in' if g["total"] < 0 else f'{money(g["total"], "ILS", 2)} out'
        rows.append(f'<tr><td dir="auto">{esc(g["description"])}</td><td class="n">{g["n"]}</td><td class="n">{esc(flow)}</td>'
                    f'<td>{form}<small>{esc(g["basis"] or "")}</small></td></tr>')
    if rows:
        out.append('<div class="card"><div style="overflow-x:auto"><table><thead><tr><th>Merchant</th><th class="n">Lines</th><th class="n">Money</th>'
                   f'<th>Category</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></div>')
    return layout(conn, ctx, "/review", "Review", "".join(out))


def items(conn: sqlite3.Connection, ctx: Ctx, q: dict) -> str:
    show = q.get("status", "open")
    statuses = {"open": ("open",), "explained": ("explained",), "all": discrepancies.STATUSES}.get(show, ("open",))
    rows = discrepancies.list_items(conn, statuses)
    tabs = " · ".join(f'<a href="/items?status={s}">{s}</a>' if s != show else f"<b>{s}</b>" for s in ("open", "explained", "all"))
    out = [f"<h1>Needs attention</h1><p>{tabs}</p>"]
    fees = [f for f in reconcile.fee_status(conn, ctx.today) if f["state"] != "refunded"]
    for f in fees:
        out.append(f'<div class="banner">Bank fee of {esc(money(f["amount"], "ILS", 2))} on {esc(f["date"])}: '
                   f'{"overdue, no refund yet" if f["state"] == "overdue" else "refund expected by " + esc(f["due"])}</div>')
    body = []
    for d in rows:
        icon = {"error": "✖", "warning": "▲", "info": "•"}[d["severity"]]
        body.append(f'<div class="item"><span class="sev" aria-hidden="true">{icon}</span> <a href="/item/{d["id"]}">{esc(d["title"])}</a>'
                    f' <small>{esc(d["status"])} · {d["comment_count"]} comment(s)'
                    f'{" · follow up " + esc(d["follow_up_date"]) if d["follow_up_date"] else ""}</small></div>')
    out.append(f'<div class="card">{"".join(body) or "Nothing here."}</div>')
    return layout(conn, ctx, "/items", "Needs attention", "".join(out))


def item(conn: sqlite3.Connection, ctx: Ctx, item_id: int) -> str:
    d = conn.execute("SELECT * FROM discrepancies WHERE id = ?", (item_id,)).fetchone()
    if not d:
        return layout(conn, ctx, "", "Not found", "<h1>Not found</h1>")
    subject = ""
    if d["subject_type"] == "transaction":
        t = conn.execute("""SELECT t.*, c.name AS cat FROM transactions t JOIN categories c ON c.id = t.category_id
                            WHERE t.id = ?""", (d["subject_id"],)).fetchone()
        if t:
            subject = (f'<p dir="auto"><b>{esc(t["description"])}</b> · {esc(t["txn_date"])} · {esc(money(t["amount"], t["currency"], 2))} · '
                       f'{esc(t["cat"])} ({esc(t["category_status"])})</p>')
    thread = "".join(f'<div class="comment"><b>{esc(c["author"])}</b> <small>{esc(c["created_at"].replace("T", " "))}'
                     f'{" · edited" if c["edited_at"] else ""}</small><div dir="auto">{esc(c["body"])}</div></div>'
                     for c in discrepancies.thread(conn, item_id))
    status_opts = "".join(f'<option{" selected" if s == d["status"] else ""}>{s}</option>' for s in discrepancies.STATUSES)
    body = (f'<p><a href="/items">← Needs attention</a></p><h1>{esc(d["title"])}</h1><div class="card">{subject}'
            f'<p>{esc(d["detail"] or "")}</p><p class="muted">Status: <b>{esc(d["status"])}</b>'
            f'{" · follow up " + esc(d["follow_up_date"]) if d["follow_up_date"] else ""}</p></div>'
            f'<div class="card" style="margin-top:12px"><h2>Comments</h2>{thread or "<p class=muted>No comments yet.</p>"}'
            + _form(ctx, f"/item/{item_id}/comment",
                    '<textarea name="body" rows="3" style="width:100%" required placeholder="What is this? What did you find out?"></textarea>'
                    f'<div class="row" style="margin-top:8px"><label>Set status <select name="status"><option value="">(unchanged)</option>{status_opts}</select></label>'
                    '<label>Follow up on <input type="date" name="follow_up"></label><button>Save comment</button></div>')
            + "</div>")
    return layout(conn, ctx, "/items", d["title"], body)


def imports(conn: sqlite3.Connection, ctx: Ctx) -> str:
    from .views_setup import dropzone
    out = ["<h1>Imports</h1>", dropzone(ctx),
           (f'<div class="card" style="margin-top:12px"><h2>Waiting</h2><p>{ctx.inbox_count} file(s) are waiting to be imported.</p>'
            + _form(ctx, "/import", "<button>Import them now</button>") + "</div>") if ctx.inbox_count else ""]
    for b in conn.execute("SELECT * FROM batches ORDER BY id DESC LIMIT 15"):
        files = [r["original_name"] for r in conn.execute("SELECT original_name FROM source_files WHERE batch_id = ?", (b["id"],))]
        n = conn.execute("SELECT COUNT(*) FROM transactions WHERE batch_id = ?", (b["id"],)).fetchone()[0]
        head = (f'<h2>Import #{b["id"]} <span class="chip">{esc(b["status"])}</span></h2>'
                f'<p class="muted">Created {esc(b["created_at"].replace("T", " "))} · {n} transactions'
                f'{" · " + esc(b["note"]) if b["note"] else ""}</p>'
                f'<small>{esc(", ".join(files)) if files else "no files (sheet history or balances)"}</small>')
        if b["status"] == "committed":
            action = (f'<p>Submitted <b>{esc(b["committed_at"].replace("T", " "))}</b></p>'
                      + _form(ctx, f"/imports/{b['id']}/reopen",
                              '<input name="reason" placeholder="Why are you reopening it?" required size="40"> <button class="quiet">Reopen</button>', "row"))
        else:
            issues = commits.readiness(conn, b["id"], ctx.today)
            lst = "".join(f"<li>{esc(i)}</li>" for i in issues[:12]) + (f"<li>and {len(issues) - 12} more</li>" if len(issues) > 12 else "")
            warn = (f'<p><b>Still open:</b></p><ul>{lst}</ul><label><input type="checkbox" name="ack" value="1"> '
                    "I know about these; submit anyway (they are recorded)</label> " if issues else "<p>Nothing open. Ready to submit.</p>")
            action = _form(ctx, f"/imports/{b['id']}/submit", warn + "<button>Submit</button>")
        out.append(f'<div class="card" style="margin-top:12px">{head}{action}</div>')
    hist = commits.history(conn)
    if hist:
        rows = "".join(f'<tr><td>#{c["batch_id"]} v{c["version"]}</td><td>{esc(c["committed_at"].replace("T", " "))}</td><td>{esc(c["reason"] or "")}</td></tr>' for c in hist)
        out.append(f'<div class="card" style="margin-top:12px"><h2>Submit history</h2><table><tbody>{rows}</tbody></table></div>')
    return layout(conn, ctx, "/imports", "Imports", "".join(out))


def balances_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    due, last = balances.balances_due(conn, ctx.today)
    rows = []
    accts = conn.execute("""SELECT a.id, a.label, a.kind, a.currency, o.name AS owner FROM accounts a
                            LEFT JOIN owners o ON o.id = a.owner_id
                            WHERE a.active = 1 AND a.kind != 'card' AND a.issuer != 'sheet' ORDER BY a.kind, a.label""").fetchall()
    latest = {r["account_id"]: r for r in balances.latest(conn, ctx.today)}
    for a in accts:
        l = latest.get(a["id"])
        stale = l and l["as_of"] < _days_ago(ctx.today, balances.STALE_DAYS)
        rows.append(f'<tr><td dir="auto">{esc(a["label"])}</td><td>{esc(a["kind"])}</td><td>{esc(a["owner"] or "")}</td>'
                    f'<td class="n">{esc(money(l["amount"], l["currency"]) if l else "–")}</td>'
                    f'<td>{esc(l["as_of"]) if l else ""}{" ▲ old" if stale else ""}</td>'
                    f'<td><input name="bal_{a["id"]}" inputmode="decimal" size="12" placeholder="new value"> {esc(a["currency"])}</td></tr>')
    banner = (f'<div class="banner">Balances are due{" (last updated " + esc(last) + ")" if last else ""}.</div>' if due else "")
    kinds = "".join(f"<option>{k}</option>" for k in ("bank", "investment", "pension", "savings", "real_estate", "loan", "other"))
    owner_opts = '<option value="">(none)</option>' + "".join(f"<option>{esc(o)}</option>" for o in ctx.owners)
    form = _form(ctx, "/balances",
                 f'<p>Date of these numbers: <input type="date" name="as_of" value="{esc(ctx.today)}"></p>'
                 f'<div style="overflow-x:auto"><table><thead><tr><th>Account</th><th>Type</th><th>Owner</th><th class="n">Last value</th><th>As of</th><th>New value</th></tr></thead>'
                 f'<tbody>{"".join(rows)}</tbody></table></div><p><button>Save the values I filled in</button> '
                 f'<span class="muted">Leave a row empty to keep its last value. Loans can be entered as positive numbers.</span></p>')
    add = _form(ctx, "/accounts", f'<div class="row"><input name="label" placeholder="Account name" required> <select name="kind">{kinds}</select> '
                f'<select name="currency"><option>ILS</option><option>USD</option></select> <select name="owner">{owner_opts}</select> <button class="quiet">Add account</button></div>')
    cur_rate = fx.current_rate(conn)
    rate_box = (f'<div class="card" style="margin-bottom:12px"><h2>Dollar rate</h2><p>Dollar accounts are valued at <b>{esc(fx_note(conn, ctx.today))}</b>. '
                "Expenses are never converted: your cards already charge shekels.</p>"
                + _form(ctx, "/fx/set", f'<label>Today\'s rate (₪ per $) <input name="rate" inputmode="decimal" size="8" value="{cur_rate["rate"]:.3f}" required></label> '
                        '<button>Use this rate</button>' if cur_rate else
                        '<label>Today\'s rate (₪ per $) <input name="rate" inputmode="decimal" size="8" required></label> <button>Use this rate</button>', "row")
                + _form(ctx, "/fx/refresh", '<button class="quiet">Fetch the current rate automatically</button>', "inline") + "</div>")
    return layout(conn, ctx, "/balances", "Balances",
                  f'<h1>Balances</h1>{banner}{rate_box}<div class="card">{form}</div><div class="card" style="margin-top:12px"><h2>New account</h2>{add}</div>')


def _days_ago(today: str, days: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(today) - timedelta(days=days)).isoformat()


def goals_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    gp = goals.all_progress(conn, ctx.today)
    cards = "".join(_goal_card(g) for g in gp) or '<p class="muted">No goals yet. Add one below.</p>'
    acct_opts = "".join(f'<label style="display:block"><input type="checkbox" name="account" value="{a["id"]}"> {esc(a["label"])}</label>'
                        for a in conn.execute("SELECT id, label FROM accounts WHERE kind NOT IN ('card') AND issuer != 'sheet' ORDER BY label"))
    owner_opts = '<option value="">Household</option>' + "".join(f"<option>{esc(o)}</option>" for o in ctx.owners)
    birth = "".join(
        _form(ctx, "/birth", f'<input type="hidden" name="owner" value="{esc(o["name"])}">{esc(o["name"])}: '
              f'<input type="date" name="birth" value="{esc(o["birth_date"] or "")}" required> <button class="quiet">Save</button>', "inline")
        + " " for o in conn.execute("SELECT name, birth_date FROM owners ORDER BY name"))
    add = _form(ctx, "/goals",
                '<div class="row"><input name="name" placeholder="Goal name" required> <input name="amount" inputmode="decimal" placeholder="Target amount" required>'
                '<select name="currency"><option>ILS</option><option>USD</option></select></div>'
                '<div class="row" style="margin-top:8px"><label>By date <input type="date" name="by"></label> <b>or</b> '
                f'<label>at age <input name="age" size="3" inputmode="numeric"></label> <label>whose <select name="owner">{owner_opts}</select></label>'
                '<label>expected yearly return % <input name="return" size="3" value="0"></label></div>'
                f'<details style="margin-top:8px"><summary>Which accounts count? (none = total net worth)</summary>{acct_opts}</details>'
                '<p><button>Add goal</button></p>')
    return layout(conn, ctx, "/goals", "Goals",
                  f'<h1>Goals</h1><div class="card">{cards}</div><div class="card" style="margin-top:12px"><h2>New goal</h2>{add}'
                  f'<p class="muted">Goals by age need a birth date: {birth}</p></div>')


def sheet_body(conn: sqlite3.Connection, ctx: Ctx, back: str = "/sheet") -> str:
    st = sheetsync.status(conn)
    head = ("" 
            + (f'<div class="card"><h2>Linked <span class="chip good"><b>✔</b> on</span></h2><p>Last updated: <b>{esc((st["last_sync"] or "never").replace("T", " "))}</b>. '
               f'It updates by itself every time you submit an import.</p><p class="muted">{esc(st["last_result"] or "")}</p>'
               + _form(ctx, "/sheet/sync", "<button>Update it now</button>", "inline") + " "
               + _form(ctx, "/sheet/notes", f'<label><input type="checkbox" name="notes" value="1" {"checked" if st["notes"] else ""} onchange="this.form.submit()"> '
                       "Also share comments on items (they may be private)</label>", "inline") + " "
               + _form(ctx, "/sheet/unlink", '<button class="quiet">Unlink</button>', "inline") + "</div>"
               if st["linked"] else
               '<div class="card"><h2>Not linked yet</h2><p>Do this once. After that the sheet updates itself after every submit, '
               "and only submitted numbers are sent.</p></div>"))
    script = esc(sheetsync.script_text(conn))
    doc = (f'<a href="{esc(st["doc_url"])}" target="_blank" rel="noopener"><b>your shared sheet</b></a>' if st["doc_url"]
           else "the Google Sheet you want to share (a new blank one is best)")
    copy_js = ("var t=document.getElementById('scr');t.select();"
               "(navigator.clipboard?navigator.clipboard.writeText(t.value):Promise.reject()).then(function(){this.textContent='Copied ✔'}.bind(this))"
               ".catch(function(){document.execCommand('copy')});")
    steps = ('<div class="card" style="margin-top:12px"><h2>Link it (3 minutes, once)</h2>'
             "<p class=muted>Google only lets a sheet's owner approve a script that writes to it, so this one step has to be yours.</p><ol>"
             f"<li>Open {doc}, then choose <b>Extensions › Apps Script</b>.</li>"
             f'<li><button onclick="{copy_js}">Copy the script</button> then delete what is in the editor, paste, and click <b>Save</b>.</li>'
             "<li><b>Deploy › New deployment</b>, type <b>Web app</b>. Execute as: <b>Me</b>. Who has access: <b>Anyone</b>. "
             "Click <b>Deploy</b> and allow it. Google warns the app is unverified because you wrote it: choose Advanced, then continue.</li>"
             "<li>Copy the <b>Web app</b> address and paste it here:</li></ol>"
             + _form(ctx, "/sheet/link", '<input name="url" style="width:100%;max-width:560px" placeholder="https://script.google.com/macros/s/…/exec" required> <button>Link and test</button>', "row")
             + '<p class="muted">The script contains a secret that only this app knows. Anyone with the address still cannot change your sheet without it.</p>'
             f'<textarea id="scr" readonly rows="8" style="width:100%;font:12px monospace" onclick="this.select()">{script}</textarea></div>')
    html = head + steps
    if back == "/sheet":
        return html
    token = f'<input type="hidden" name="csrf" value="{esc(ctx.csrf)}">'
    return html.replace(token, token + f'<input type="hidden" name="back" value="{esc(back)}">')


def sheet_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    return layout(conn, ctx, "/sheet", "Google Sheet", "<h1>Google Sheet</h1>" + sheet_body(conn, ctx))
