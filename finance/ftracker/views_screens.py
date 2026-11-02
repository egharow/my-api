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
    wait = screens.waiting(conn, home, watchfolder.get(conn))
    ready, why = ocr.status()
    reader = ('<p class="chip good"><b>✔</b> Text recognition is on</p>' if ready else
              f'<p class="muted">Text recognition: {esc(why)}</p>'
              + (_form(ctx, "/screens/install", '<button>Turn on text recognition</button> <span class="cap">One-time download of about 10 MB. Needs the internet.</span>', "row")
                 if ocr.sys.platform == "win32" else ""))
    mine = "".join(f'<div class="srcrow"><span dir="auto">{esc(n)}</span><span><a class="btnlink" href="/screen/{esc(n)}">Read it</a></span></div>' for n in wait["mine"])
    drive = "".join(f'<div class="srcrow"><span dir="auto">{esc(n)}</span>'
                    + _form(ctx, "/screens/take", f'<input type="hidden" name="name" value="{esc(n)}"><button class="quiet">Read it</button>', "inline") + "</div>"
                    for n in wait["drive"])
    js = ("(function(){var f=document.getElementById('shotfile'),csrf=f.getAttribute('data-csrf');f.addEventListener('change',function(){"
          "if(!f.files.length)return;var fd=new FormData();for(var i=0;i<f.files.length;i++)fd.append('file',f.files[i]);"
          "fetch('/screens/upload',{method:'POST',headers:{'X-CSRF':csrf},body:fd}).then(function(r){return r.json()}).then(function(j){"
          "if(j.error){alert(j.error)}else if(j.saved&&j.saved.length){location.href='/screen/'+encodeURIComponent(j.saved[0])}else{location.reload()}})})})();")
    body = ('<h1>Balances from a screenshot</h1><p class="muted">Add a screenshot of your balances, for example from the One Zero wealth screen. '
            'The app reads the numbers and shows them next to the picture. Nothing is saved until you approve it.</p>'
            f'<div class="card">{reader}<p><label class="btnlink" style="cursor:pointer">Choose a screenshot<input id="shotfile" type="file" accept="image/*" multiple hidden '
            f'data-csrf="{esc(ctx.csrf)}"></label></p></div>'
            + (f'<div class="card" style="margin-top:14px"><h2>Waiting</h2>{mine}</div>' if mine else "")
            + (f'<div class="card" style="margin-top:14px"><h2>Pictures in your Drive folder</h2>{drive}</div>' if drive else "")
            + f"<script>{js}</script>")
    return layout(conn, ctx, "/balances", "Balances from a screenshot", body)


def review_page(conn: sqlite3.Connection, ctx: Ctx, home: Home, name: str) -> str:
    safe = screens.safe_name(name)
    path = screens.dir_of(home) / (safe or "")
    if not safe or not path.is_file():
        return layout(conn, ctx, "/balances", "Screenshot", '<h1>Not found</h1><p><a href="/screens">Back</a></p>')
    words, note = ocr.read_words(path)
    found = screens.extract(words) if words else []
    accts = _accounts(conn, ctx.today)
    opts = "".join(f'<option value="{a["id"]}">{esc(a["label"])}{" · " + esc(a["owner"]) if a["owner"] else ""} ({esc(a["currency"])})</option>' for a in accts)

    def row(i, r):
        pick = None if r["total"] else screens.suggest(conn, r["section"], r["label"])
        sel = opts.replace(f'value="{pick}"', f'value="{pick}" selected') if pick else opts
        shown = f'{r["amount"]:,.2f}'
        chips = ' <span class="chip">total</span>' if r["total"] else (' <span class="chip">dollars</span>' if r["currency"] == "USD" else "")
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
    return layout(conn, ctx, "/balances", "Screenshot",
                  f'<p><a href="/screens">← Screenshots</a></p><h1>Check the numbers against the picture</h1>{notice}'
                  f'<div class="shotgrid"><div class="shotimg"><a href="/screens/file/{esc(safe)}" target="_blank" rel="noopener">'
                  f'<img src="/screens/file/{esc(safe)}" alt="Your screenshot"></a><p class="cap">Click the picture to open it larger.</p></div>'
                  f'<div class="card">{form}</div></div>')
