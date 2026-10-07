"""The pages you actually use: Home (this round's steps), Upload (with the checklist), Spending and Wealth."""
import sqlite3
from html import escape as esc

from . import balances, charts, expected, goals, summary, watchfolder
from .charts import money
from .views import Ctx, _form, _goal_card, _month_window, _net_worth_series, _tile, counts, layout
from .views_trends import networth_body, trends_body

MARK = {"done": ("✔", "good", "Received"), "todo": ("☐", "serious", "Needed"), "waiting": ("◌", "muted", "Not due yet"),
        "optional": ("○", "muted", "Optional")}


def _group_html(g: dict) -> str:
    rows = []
    for i in g["items"]:
        icon, tone, word = MARK[i["status"]]
        name = f'<a href="{esc(i["link"])}">{esc(i["label"])}</a>' if i.get("link") and i["status"] == "todo" else esc(i["label"])
        detail = f'{esc(word)} · {esc(i["detail"])}'
        if i.get("children"):
            inner = "".join(
                f'<div class="row" style="padding:2px 0"><span class="chip {MARK[k["status"]][1]}"><b aria-hidden="true">{MARK[k["status"]][0]}</b></span>'
                f'<span dir="auto">{esc(k["label"])}</span><span class="muted">{esc(k["detail"])}</span></div>' for k in i["children"])
            detail = f'<details{" open" if i["status"] == "todo" else ""}><summary>{detail} · show which</summary>{inner}</details>'
        rows.append(f'<tr><td style="width:2em"><span class="chip {tone}"><b aria-hidden="true">{icon}</b></span></td>'
                    f'<td dir="auto">{name}</td><td class="muted">{detail}</td></tr>')
    return f'<h3 style="margin:10px 0 4px">{esc(g["title"])}</h3><table><tbody>{"".join(rows)}</tbody></table>'


def render_checklist(cl: dict) -> str:
    """One collapsible block per month, the current month first, so September and October are never mixed."""
    out = []
    for n, mth in enumerate(cl["by_month"]):
        complete = mth["done"] == mth["total"]
        chip = ('<span class="chip good"><b aria-hidden="true">✔</b></span>' if complete
                else '<span class="chip serious"><b aria-hidden="true">☐</b></span>')
        opened = n == 0 or not complete
        out.append(f'<details{" open" if opened else ""} style="padding:6px 0;border-bottom:1px solid var(--grid)"><summary style="font-size:16px">{chip} '
                   f'<b>{esc(mth["label"])}</b> <span class="muted">· {mth["done"]} of {mth["total"]} received</span></summary>'
                   + "".join(_group_html(g) for g in mth["groups"]) + "</details>")
    return "".join(out)


def render_history(months: list[dict]) -> str:
    if not months:
        return ""
    blocks = []
    for mth in months:
        complete = mth["done"] == mth["total"]
        chip = ('<span class="chip good"><b aria-hidden="true">✔</b></span>' if complete
                else '<span class="chip serious"><b aria-hidden="true">☐</b></span>')
        rows = "".join(
            f'<tr><td style="width:2em"><span class="chip {MARK[i["status"]][1]}"><b aria-hidden="true">{MARK[i["status"]][0]}</b></span></td>'
            f'<td dir="auto">{esc(i["label"])}</td><td class="muted">{esc(MARK[i["status"]][2])} · {esc(i["detail"])}</td></tr>' for i in mth["items"])
        blocks.append(f'<details{"" if complete else " open"} style="padding:6px 0;border-bottom:1px solid var(--grid)"><summary>{chip} <b>{esc(mth["label"])}</b> '
                      f'<span class="muted">· {mth["done"]} of {mth["total"]} received</span></summary><table><tbody>{rows}</tbody></table></details>')
    return f'<section class="card" style="margin-top:16px"><h2>Earlier months</h2>{"".join(blocks)}</section>'


def _step(n: int, title: str, state: str, detail: str, href: str, button: str) -> str:
    icon, tone = {"done": ("✔", "good"), "todo": ("☐", "serious"), "idle": ("○", "muted")}[state]
    return (f'<div class="item"><div class="row"><span class="chip {tone}"><b aria-hidden="true">{icon}</b></span>'
            f'<b>{n}. {esc(title)}</b><span class="muted" style="flex:1">{esc(detail)}</span>'
            f'<a href="{href}"><button class="{"" if state == "todo" else "quiet"}">{esc(button)}</button></a></div></div>')


def home(conn: sqlite3.Connection, ctx: Ctx) -> str:
    from . import firstrun, sheetsync
    c = counts(conn)
    cl = expected.checklist(conn, ctx.today)
    bal = [i for g in cl["groups"] for i in g["items"] if i.get("children")]
    due_b = bool(bal) and bal[0]["status"] == "todo"
    bal_detail = bal[0]["detail"] if bal else ""
    drafts = conn.execute("SELECT COUNT(*) FROM batches WHERE status != 'committed'").fetchone()[0]
    missing = sum(1 for g in cl["groups"] for i in g["items"] if i["status"] == "todo" and not i.get("children"))
    steps = "".join([
        _step(1, "Upload your statements", "done" if not missing else "todo",
              f"{missing} statement(s) still to upload" if missing else "all statements received", "/upload", "Open the checklist"),
        _step(2, "Confirm categories", "done" if not c["review"] else "todo",
              "nothing to confirm" if not c["review"] else f"{c['review']} merchants to confirm", "/review", "Review"),
        _step(3, "Check for problems", "done" if not c["items"] else "todo",
              "all clear" if not c["items"] else f"{c['items']} to look at", "/items", "See them"),
        _step(4, "Update balances (monthly)", "todo" if due_b else "done", bal_detail, "/balances", "Enter balances"),
        _step(5, "Submit", "todo" if drafts else "done",
              f"{drafts} import(s) not submitted yet" if drafts else "everything is recorded", "/imports", "Submit"),
    ])
    out = ['<h1>This round</h1>']
    loaded = firstrun.pending_notice(conn)
    if loaded:
        out.append('<div class="banner" style="border-color:var(--good)"><b>Your starting data is loaded.</b><ul>'
                   + "".join(f"<li>{esc(x)}</li>" for x in loaded) + "</ul>"
                   + _form(ctx, "/seed/dismiss", "<button class=quiet>Got it</button>", "inline") + "</div>")
    out.append(f'<section class="card">{steps}</section>')

    pts, no_rate = _net_worth_series(conn, "ILS")
    months = [m for m in summary.months_available(conn) if summary.spending_by_category(conn, m)]
    last_m = months[-1] if months else None
    ov = summary.month_overview(conn, last_m) if last_m else None
    gp = goals.all_progress(conn, ctx.today)
    on = sum(1 for g in gp if g["status"] in ("on_track", "reached"))
    chg = f"{money(pts[-1][1] - pts[-2][1])} since {pts[-2][0]}" if len(pts) > 1 else ""
    out.append('<div class="tiles" style="margin-top:16px">'
               + _tile("Net worth", money(pts[-1][1]) if pts else "–",
                     '<a href="/balances">needs a dollar rate</a>' if no_rate else f'<a href="/wealth">{esc(chg) or "Wealth"}</a>')
               + _tile(f"Spending {last_m or ''}", money(ov["spending"]) if ov else "–", '<a href="/spending">Spending</a>')
               + _tile("Goals on track", f"{on} of {len(gp)}" if gp else "–", '<a href="/goals">Goals</a>') + "</div>")
    st = sheetsync.status(conn)
    if st["linked"] and st["last_result"] and st["last_result"].startswith("failed"):
        out.append(f'<div class="banner">The Google Sheet did not update: {esc(st["last_result"][8:])} <a href="/sheet">Details</a></div>')
    return layout(conn, ctx, "/", "Home", "".join(out))


def upload_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    from .views_setup import dropzone
    cl = expected.checklist(conn, ctx.today)
    c = counts(conn)
    done = cl["done"] == cl["total"]
    head = (f'<div class="banner" style="border-color:var(--good)"><b>Everything on the list is in.</b></div>' if done else
            f'<p class="muted">{cl["done"]} of {cl["total"]} received. Drop a file and its box is ticked automatically.</p>')
    folder = watchfolder.get(conn)
    watch = (f'<p class="muted">Also picking up statements from <code>{esc(folder)}</code>. ' + _form(ctx, "/watch/check", '<button class="quiet">Check it now</button>', "inline") + "</p>"
             if folder else '<p class="muted">Tip: statements saved to a Google Drive folder can be picked up automatically. '
             '<a href="/setup">Set it up in Settings</a>.</p>')
    nxt = (f'<p><a href="/review"><button>Next: confirm {c["review"]} categories</button></a></p>' if c["review"] else
           '<p><a href="/"><button class="quiet">Back to this round</button></a></p>')
    from .views import sheet_body
    body = (f'<h1>Upload</h1>{dropzone(ctx, compact=True)}{watch}<section class="card">{head}{render_checklist(cl)}</section>{render_history(expected.history(conn, ctx.today))}'
            f'<div style="margin-top:12px">{nxt}</div>'
            f'<section style="margin-top:24px"><h2>Google Sheet (optional)</h2>{sheet_body(conn, ctx, "/upload")}</section>')
    return layout(conn, ctx, "/upload", "Upload", body)


def spending_page(conn: sqlite3.Connection, ctx: Ctx, q: dict) -> str:
    body = trends_body(conn, ctx, q, "/spending")
    months = summary.months_available(conn)[-18:]
    ov = [summary.month_overview(conn, m) for m in months]
    ov = [o for o in ov if o["spending"] or o["income"]]
    window = _month_window([o["month"] for o in ov], 120)
    by = {o["month"]: o for o in ov}
    inc = [by[m]["income"] if m in by else None for m in window]
    spent = [by[m]["spending"] if m in by else None for m in window]
    extra = (f'<section class="card" style="margin-top:16px"><h2>Income and spending</h2>{charts.grouped_bars(window, inc, spent)}'
             f'{charts.data_table(["Month", "Income", "Spending"], [[m, f"{i or 0:,.0f}", f"{s or 0:,.0f}"] for m, i, s in zip(window, inc, spent)], "Income and spending by month")}</section>'
             ) if window else ""
    return layout(conn, ctx, "/spending", "Spending", f"<h1>Spending</h1>{body}{extra}")


def wealth_page(conn: sqlite3.Connection, ctx: Ctx, q: dict) -> str:
    body = networth_body(conn, ctx, q, "/wealth")
    gp = goals.all_progress(conn, ctx.today)
    cards = "".join(_goal_card(g) for g in gp)
    goals_html = (f'<section class="card" style="margin-top:16px"><h2>Goals <small><a href="/goals">edit</a></small></h2>{cards}</section>'
                  if gp else '<section class="card" style="margin-top:16px"><h2>Goals</h2><p class="muted">No goals yet. <a href="/goals">Add one</a>.</p></section>')
    return layout(conn, ctx, "/wealth", "Wealth", f"<h1>Wealth</h1>{body}{goals_html}")
