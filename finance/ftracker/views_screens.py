"""Balances from a screenshot, with the picture beside what was read from it."""
import sqlite3
from html import escape as esc
from pathlib import Path

from . import ocr, screens, watchfolder
from .charts import money
from .config import Home
from .views import Ctx, _form, dmy, layout


def _accounts(conn: sqlite3.Connection, today: str):
    return conn.execute(
        """SELECT a.id, a.label, a.currency, a.kind, o.name AS owner,
                  (SELECT b.amount FROM balances b WHERE b.account_id = a.id ORDER BY b.as_of DESC LIMIT 1) AS last,
                  (SELECT b.as_of FROM balances b WHERE b.account_id = a.id ORDER BY b.as_of DESC LIMIT 1) AS last_on
           FROM accounts a LEFT JOIN owners o ON o.id = a.owner_id
           WHERE a.active = 1 AND a.kind != 'card' AND a.issuer NOT IN ('sheet', 'cash') AND (a.closed_on IS NULL OR a.closed_on > ?)
           ORDER BY a.sort_order, o.name, a.label""", (today,)).fetchall()


def list_page(conn: sqlite3.Connection, ctx: Ctx, home: Home) -> str:
    names = screens.waiting_names(home)
    ready, why = ocr.status()
    reader = ('<p class="chip good"><b>✔</b> Text recognition is on</p>' if ready else
              f'<p class="muted">Text recognition: {esc(why)}</p>'
              + (_form(ctx, "/screens/install", '<button>Turn on text recognition</button> <span class="cap">One-time download of about 10 MB. Needs the internet.</span>', "row")
                 if ocr.sys.platform == "win32" else ""))
    rows = "".join(f'<div class="srcrow"><span dir="auto">{esc(n)}</span><span><a class="btnlink" href="/screen/{esc(n)}">Check it</a></span></div>' for n in names)
    body = ('<h1>Balance screenshots</h1><p class="muted">Save a screenshot of your balances in the same Drive folder as your statements, or drop it on the Upload page. '
            'It shows up in your to-do list. The numbers are read, shown beside the picture, and saved only when you approve them.</p>'
            f'<div class="card">{reader}</div><div class="card" style="margin-top:14px"><h2>Waiting for you</h2>{rows or "<p class=muted>None.</p>"}</div>')
    return layout(conn, ctx, "/screens", "Balance screenshots", body)


def review_page(conn: sqlite3.Connection, ctx: Ctx, home: Home, name: str) -> str:
    safe = screens.safe_name(name)
    path = screens.dir_of(home) / (safe or "")
    if not safe or not path.is_file():
        return layout(conn, ctx, "/screens", "Screenshot", '<h1>Not found</h1><p><a href="/screens">Back</a></p>')
    words, note = ocr.read_words(path)
    found = screens.extract(words) if words else []
    accts = _accounts(conn, ctx.today)
    opts = "".join(f'<option value="{a["id"]}">{esc(a["label"])}{" · " + esc(a["owner"]) if a["owner"] else ""} ({esc(a["currency"])})</option>' for a in accts)

    picks = screens.suggest_all(conn, found, ctx.today)

    def row(i, r):
        pick, how = picks[i]
        sel = opts.replace(f'value="{pick}"', f'value="{pick}" selected') if pick else opts
        shown = f'{r["amount"]:,.2f}'
        chips = ' <span class="chip">total</span>' if r["total"] else (' <span class="chip">dollars</span>' if r["currency"] == "USD" else "")
        chips += ' <span class="chip" title="Matched because the balance is close to the one you have. Check it.">guess</span>' if how == "balance" else ""
        return (f'<tr><td><input type="checkbox" name="r{i}_on" value="1"{" checked" if pick else ""} aria-label="Save"></td>'
                f'<td dir="auto"><b>{esc(r["label"] or "(no name)")}</b>{chips}<div class="cap" dir="auto">{esc(r["section"])}</div>'
                f'<input type="hidden" name="r{i}_label" value="{esc(r["label"])}"><input type="hidden" name="r{i}_section" value="{esc(r["section"])}"><input type="hidden" name="r{i}_cur" value="{r["currency"]}"></td>'
                f'<td><input name="r{i}_amount" value="{shown}" inputmode="decimal" size="12" style="text-align:right"></td>'
                f'<td><select name="r{i}_account"><option value="">choose the account…</option>{sel}</select></td></tr>')
    rows = "".join(row(i, r) for i, r in enumerate(found))
    read = (f'<table><thead><tr><th></th><th>Read from the picture</th><th>Amount</th><th>Goes to</th></tr></thead><tbody>{rows}</tbody></table>'
            if rows else '<p class="muted">Nothing could be read automatically. Type the numbers you see in the list below.</p>')
    manual = "".join(
        f'<tr><td dir="auto">{esc(a["label"])}{" · " + esc(a["owner"]) if a["owner"] else ""}</td><td class="n cap">{money(a["last"], a["currency"]) if a["last"] is not None else "–"}'
        f'<br>{esc(dmy(a["last_on"])) if a["last_on"] else ""}</td><td><input name="m_{a["id"]}" inputmode="decimal" size="12" style="text-align:right" placeholder="{esc(a["currency"])}"></td></tr>'
        for a in accts)
    form = _form(ctx, "/screens/approve",
                 f'<input type="hidden" name="image" value="{esc(safe)}"><input type="hidden" name="n" value="{len(found)}">'
                 f'<p><label>Date of these balances <input type="date" name="as_of" value="{esc(ctx.today)}"></label></p>{read}'
                 f'<details style="margin-top:12px"{"" if rows else " open"}><summary><b>Type balances yourself</b> <span class="cap">for accounts that were not read</span></summary>'
                 f'<table><thead><tr><th>Account</th><th class="n">Last value</th><th>New value</th></tr></thead><tbody>{manual}</tbody></table></details>'
                 '<p class="row" style="margin-top:12px"><button>Approve and save the ticked balances</button> '
                 f'<button class="quiet" formaction="/screens/skip">Not now, I am done with this picture</button></p>')
    notice = f'<p class="banner">{esc(note)}</p>' if note else ""
    return layout(conn, ctx, "/screens", "Screenshot",
                  f'<p><a href="/screens">← Screenshots</a></p><h1>Check the numbers against the picture</h1>{notice}'
                  f'<div class="shotgrid"><div class="shotimg"><a href="/screens/file/{esc(safe)}" target="_blank" rel="noopener">'
                  f'<img src="/screens/file/{esc(safe)}" alt="Your screenshot"></a><p class="cap">Click the picture to open it larger.</p></div>'
                  f'<div class="card">{form}</div></div>')
