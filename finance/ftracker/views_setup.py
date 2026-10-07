"""Pages that replace the old command-line steps: dropping files, importing the old sheet, and setup."""
import sqlite3
from html import escape as esc

from . import categorize, charts, sheet_import, watchfolder
from .views import Ctx, _form, layout

DROP_JS = """
(function(){
var z=document.getElementById('drop'),pick=document.getElementById('pick'),csrf=z.getAttribute('data-csrf');
function send(files){if(!files.length)return;z.classList.add('busy');z.querySelector('b').textContent='Uploading '+files.length+' file(s)…';
var fd=new FormData();for(var i=0;i<files.length;i++)fd.append('file',files[i]);
fetch('/upload',{method:'POST',headers:{'X-CSRF':csrf},body:fd}).then(function(r){return r.json()}).then(function(j){
if(j.error){alert(j.error);z.classList.remove('busy');return}
if(j.rejected&&j.rejected.length){alert('Not imported:\\n'+j.rejected.join('\\n'))}
if(j.saved&&j.saved.length){document.getElementById('importform').submit()}else{z.classList.remove('busy');z.querySelector('b').textContent='Drop statements here'}
}).catch(function(){alert('Upload failed. Is the app still running?');z.classList.remove('busy')})}
['dragenter','dragover'].forEach(function(e){z.addEventListener(e,function(ev){ev.preventDefault();z.classList.add('over')})});
['dragleave','drop'].forEach(function(e){z.addEventListener(e,function(ev){ev.preventDefault();z.classList.remove('over')})});
z.addEventListener('drop',function(ev){send(ev.dataTransfer.files)});
pick.addEventListener('change',function(){send(pick.files)});
document.addEventListener('dragover',function(e){e.preventDefault()});document.addEventListener('drop',function(e){e.preventDefault()});
})();
"""


def dropzone(ctx: Ctx, compact: bool = False) -> str:
    style = "padding:14px" if compact else "padding:34px"
    return (f'<div class="card drop" id="drop" data-csrf="{esc(ctx.csrf)}" style="{style};text-align:center;border-style:dashed;margin-bottom:16px">'
            '<b>Drop statements here</b><div class="muted">card exports (.xlsx, .xls) and bank statements (.pdf), or '
            '<label style="color:var(--accent);cursor:pointer;text-decoration:underline">choose files'
            '<input type="file" id="pick" multiple accept=".xlsx,.xls,.pdf" hidden></label></div></div>'
            f'<form id="importform" method="post" action="/import"><input type="hidden" name="csrf" value="{esc(ctx.csrf)}"></form>'
            f"<script>{DROP_JS}</script>")


# ---- old sheet ----------------------------------------------------------------------------------

HISTORY_JS = """
(function(){var f=document.getElementById('hfile'),b=document.getElementById('hbtn'),c=b.getAttribute('data-csrf');
b.addEventListener('click',function(){if(!f.files.length){alert('Choose the downloaded Excel file first.');return}
b.disabled=true;b.textContent='Reading…';var fd=new FormData();fd.append('file',f.files[0]);
fetch('/history/upload',{method:'POST',headers:{'X-CSRF':c},body:fd}).then(function(r){return r.json()}).then(function(j){
if(j.error){alert(j.error);b.disabled=false;b.textContent='Read it';return}location.href='/history?t='+j.token}).catch(function(){alert('Upload failed.');b.disabled=false;b.textContent='Read it'})})})();
"""


def history_upload_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    done = conn.execute("SELECT COUNT(*) FROM transactions WHERE account_id IN (SELECT id FROM accounts WHERE issuer = 'sheet')").fetchone()[0]
    note = (f'<div class="banner">{done} lines from the old sheet are already imported. Importing the same file again adds nothing twice.</div>' if done else "")
    return layout(conn, ctx, "/setup", "Import old sheet",
                  f'<h1>Import the old budget sheet</h1>{note}<div class="card"><ol>'
                  "<li>In Google Sheets, open the old budget and choose <b>File › Download › Microsoft Excel (.xlsx)</b>.</li>"
                  "<li>Choose that downloaded file below. You will see a preview first; nothing is saved until you press Save.</li></ol>"
                  '<input type="file" id="hfile" accept=".xlsx"> '
                  f'<button id="hbtn" data-csrf="{esc(ctx.csrf)}">Read it</button><script>{HISTORY_JS}</script></div>')


def history_preview_page(conn: sqlite3.Connection, ctx: Ctx, data: "sheet_import.HistoryData", token: str) -> str:
    if not data.months:
        return layout(conn, ctx, "/setup", "Import old sheet",
                      '<h1>Import the old budget sheet</h1><div class="card">No monthly tabs were found in that file. '
                      'Is it the right spreadsheet? <a href="/history">Try another</a></div>')
    n_lines = sum(len(m.log) for m in data.months)
    gaps = sheet_import._missing_months(data)
    tiles = ('<div class="tiles">'
             f'<div class="tile"><div class="l">Months</div><div class="v">{len(data.months)}</div><div class="d">{esc(data.months[0].month)} to {esc(data.months[-1].month)}</div></div>'
             f'<div class="tile"><div class="l">Expense lines</div><div class="v">{n_lines:,}</div></div>'
             f'<div class="tile"><div class="l">Accounts</div><div class="v">{len(data.net_worth)}</div><div class="d">{len(data.net_worth_dates)} balance dates</div></div>'
             f'<div class="tile"><div class="l">Months with no tab</div><div class="v">{len(gaps)}</div><div class="d">{esc(", ".join(gaps[:4]))}</div></div></div>')
    rows = "".join(
        f'<tr><td>{esc(m.month)}</td><td dir="auto">{esc(m.tab)}</td><td class="n">{len(m.log)}</td>'
        f'<td class="n">{sum(l.amount for l in m.log):,.0f}</td><td class="n">{sum(1 for l in m.log if not l.category)}</td></tr>' for m in data.months)
    table = charts.scroll('<table><thead><tr><th>Month</th><th>Tab</th><th class="n">Lines</th><th class="n">Total ₪</th>'
                          f'<th class="n">No category</th></tr></thead><tbody>{rows}</tbody></table>')
    undated = sum(1 for w in data.warnings if "no date" in w)
    other = [w for w in data.warnings if "no date" not in w]
    warn = ""
    if undated or other:
        items = ([f"Lines without a date are booked on the 1st of their month (in {undated} tab(s)). Nothing is lost."] if undated else []) + other
        warn = '<div class="banner"><b>Worth knowing</b><ul>' + "".join(f"<li>{esc(w)}</li>" for w in items) + "</ul></div>"
    cats = conn.execute("SELECT name FROM categories WHERE name != 'Uncategorised' ORDER BY kind, name").fetchall()
    unmapped = sheet_import.unmapped_categories(data)
    map_rows = ""
    for i, (name, count) in enumerate(unmapped.most_common()):
        opts = '<option value="">keep as its own category</option>' + "".join(f"<option>{esc(c[0])}</option>" for c in cats)
        map_rows += (f'<tr><td dir="auto"><b>{esc(name)}</b> <small>{count} line(s)</small></td><td>'
                     f'<input type="hidden" name="src_{i}" value="{esc(name)}"><select name="dst_{i}">{opts}</select></td></tr>')
    mapping = (f'<h2>Categories I did not recognise</h2><p class="muted">Pick an existing category to merge them into, or keep them as they are.</p>'
               f"<table><tbody>{map_rows}</tbody></table>" if map_rows else "")
    owners = [r[0] for r in conn.execute("SELECT name FROM owners ORDER BY name")]
    if not owners:
        return layout(conn, ctx, "/setup", "Import old sheet", '<h1>Import the old budget sheet</h1><div class="banner">Add the people first on the '
                      '<a href="/setup">Setup</a> page (at least your own name), then come back.</div>')
    o1 = "".join(f'<option{" selected" if i == 0 else ""}>{esc(o)}</option>' for i, o in enumerate(owners))
    o2 = "".join(f'<option{" selected" if i == 1 else ""}>{esc(o)}</option>' for i, o in enumerate(owners))
    form = _form(ctx, "/history/save",
                 f'<input type="hidden" name="t" value="{esc(token)}"><input type="hidden" name="n_maps" value="{len(unmapped)}">{mapping}'
                 f'<h2>Whose is it?</h2><p>The first income and savings rows in your sheet are <select name="primary">{o1}</select> and '
                 f'<select name="partner">{o2}</select>. Accounts under “Shir and the children” belong to the second person.</p>'
                 '<p><button>Save the old history</button> <span class="muted">It is saved as a draft you can review before submitting.</span></p>')
    return layout(conn, ctx, "/setup", "Import old sheet",
                  f'<h1>Preview of the old sheet</h1>{tiles}{warn}<div class="card">{table}</div>'
                  f'<div class="card" style="margin-top:12px">{form}</div>')


# ---- setup --------------------------------------------------------------------------------------

def setup_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    out = ["<h1>Settings</h1>",
           '<div class="card" style="margin-bottom:12px"><div class="row"><a href="/items">Problems to check</a> · <a href="/imports">Imports and submit history</a> · '
           '<a href="/balances">Balances and dollar rate</a> · <a href="/goals">Goals</a> · <a href="/sheet">Google Sheet (optional)</a></div></div>']
    if ctx.starter:
        out.append('<div class="banner"><b>Your starter setup is ready.</b> It adds the people, accounts and income rules prepared for you. '
                   + _form(ctx, "/setup/starter", "<button>Apply it</button>", "inline") + "</div>")

    owners = conn.execute("SELECT id, name, birth_date FROM owners ORDER BY name").fetchall()
    people = ""
    for o in owners:
        aliases = conn.execute("SELECT id, alias FROM owner_aliases WHERE owner_id = ?", (o["id"],)).fetchall()
        chips = " ".join(_form(ctx, "/setup/alias/remove", f'<input type="hidden" name="id" value="{a["id"]}"><span dir="auto">{esc(a["alias"])}</span> '
                               '<button class="quiet" title="Remove">×</button>', "inline") for a in aliases) or '<span class="muted">none yet</span>'
        add = _form(ctx, "/setup/alias", f'<input type="hidden" name="owner" value="{esc(o["name"])}"><input name="alias" placeholder="name as printed on statements" size="28" required> '
                    '<button class="quiet">Add</button>', "row")
        people += f'<tr><td><b>{esc(o["name"])}</b></td><td>{chips}</td><td>{add}</td></tr>'
    out.append('<section class="card"><h2>People</h2><p class="muted">A statement is assigned to a person when it carries their name as you type it here '
               '(only the name is matched; it is never stored).</p>'
               + (charts.scroll(f'<table><thead><tr><th>Person</th><th>Names on statements</th><th>Add a name</th></tr></thead><tbody>{people}</tbody></table>') if owners else "<p>No one yet.</p>")
               + _form(ctx, "/setup/owner", '<input name="name" placeholder="New person (for example Shir)" required> <button class="quiet">Add person</button>', "row") + "</section>")

    accts = conn.execute("""SELECT a.id, a.label, a.kind, a.currency, o.name AS owner FROM accounts a LEFT JOIN owners o ON o.id = a.owner_id
                            WHERE a.issuer != 'sheet' ORDER BY a.kind, a.label""").fetchall()
    arows = ""
    for a in accts:
        opts = '<option value="">(nobody)</option>' + "".join(f'<option{" selected" if o["name"] == a["owner"] else ""}>{esc(o["name"])}</option>' for o in owners)
        arows += (f'<tr><td dir="auto">{esc(a["label"])}</td><td>{esc(a["kind"])}</td><td>{esc(a["currency"])}</td><td>'
                  + _form(ctx, "/setup/account-owner", f'<input type="hidden" name="id" value="{a["id"]}"><select name="owner">{opts}</select> <button class="quiet">Save</button>', "row")
                  + "</td></tr>")
    out.append('<section class="card" style="margin-top:12px"><h2>Accounts and cards</h2><p class="muted">Cards and bank accounts appear here after their first statement. '
               'Add savings, pension, investment and property accounts on the <a href="/balances">Balances</a> page.</p>'
               + (charts.scroll(f'<table><thead><tr><th>Account</th><th>Type</th><th>Currency</th><th>Belongs to</th></tr></thead><tbody>{arows}</tbody></table>') if accts else "<p>None yet.</p>") + "</section>")

    cats = conn.execute("SELECT name FROM categories WHERE name != 'Uncategorised' ORDER BY kind, name").fetchall()
    cat_opts = "".join(f"<option>{esc(c[0])}</option>" for c in cats)
    dest_opts = '<option value="">(choose)</option>' + "".join(
        f'<option value="{a["id"]}">{esc(a["label"])}</option>' for a in accts if a["kind"] in ("savings", "bank", "investment", "pension", "other"))
    add_rule = _form(ctx, "/setup/rule",
                     '<div class="row"><input name="pattern" placeholder="Words on the statement line" size="26" required>'
                     '<select name="mode"><option value="spend">Spending, category →</option><option value="income">Money coming in, category →</option>'
                     '<option value="transfer_household">Transfer to my household (not spending)</option><option value="transfer_account">Transfer to this account →</option></select>'
                     f'<select name="category">{cat_opts}</select><select name="destination">{dest_opts}</select><button>Add rule</button></div>')
    rules = conn.execute("""SELECT r.id, r.source, r.pattern, r.enabled, c.name AS category, r.note FROM rules r JOIN categories c ON c.id = r.category_id
                            ORDER BY r.source = 'builtin', r.source, r.pattern""").fetchall()
    by = {"user": [], "learned": [], "builtin": []}
    for r in rules:
        by[r["source"]].append(r)

    def rule_rows(rs, limit=None):
        out_rows = ""
        for r in rs[:limit]:
            out_rows += (f'<tr><td dir="auto">{esc(r["pattern"])}</td><td>{esc(r["category"])}</td><td>'
                         + _form(ctx, "/setup/rule/toggle", f'<input type="hidden" name="id" value="{r["id"]}"><button class="quiet">{"On" if r["enabled"] else "Off"}</button>', "inline")
                         + ("" if r["source"] == "builtin" else " " + _form(ctx, "/setup/rule/delete", f'<input type="hidden" name="id" value="{r["id"]}"><button class="quiet">Delete</button>', "inline"))
                         + "</td></tr>")
        return charts.scroll(f'<table><thead><tr><th>When the line contains</th><th>Category</th><th></th></tr></thead><tbody>{out_rows}</tbody></table>')
    out.append('<section class="card" style="margin-top:12px"><h2>Rules</h2><p class="muted">The app applies these to every new statement line. '
               'Your own rules win over learned ones, and learned ones win over built-in ones.</p>' + add_rule
               + f"<h2 style='margin-top:16px'>Your rules ({len(by['user'])})</h2>" + (rule_rows(by["user"]) if by["user"] else "<p class=muted>None yet.</p>")
               + f"<details style='margin-top:12px'><summary>Learned from your history and approvals ({len(by['learned'])})</summary>{rule_rows(by['learned'], 200)}</details>"
               + f"<details style='margin-top:8px'><summary>Built in ({len(by['builtin'])})</summary>{rule_rows(by['builtin'])}</details></section>")

    cc = categorize.category_counts(conn)
    cat_rows = "".join(f'<tr><td dir="auto">{esc(c["name"])}</td><td>{esc(c["kind"])}</td><td class="n">{c["lines"]}</td></tr>' for c in cc)
    pick = lambda name, skip=False: (f'<select name="{name}" required>' + "".join(
        f'<option>{esc(c["name"])}</option>' for c in cc if not (skip and c["name"] == "Uncategorised")) + "</select>")
    out.append('<section class="card" style="margin-top:12px"><h2>Categories</h2><p class="muted">Merge a category into another (all its lines and rules move), '
               'or rename one. Use this to tidy names from your old sheet.</p>'
               + _form(ctx, "/setup/category/merge", f"Merge {pick('src', True)} into {pick('dst')} <button>Merge</button>", "row")
               + _form(ctx, "/setup/category/rename", f"Rename {pick('old', True)} to <input name='new' placeholder='new name' required> <button class=quiet>Rename</button>", "row")
               + f'<details style="margin-top:12px"><summary>All categories ({len(cc)})</summary>'
               + charts.scroll(f'<table><thead><tr><th>Category</th><th>Type</th><th class="n">Lines</th></tr></thead><tbody>{cat_rows}</tbody></table>') + "</details></section>")
    cards = conn.execute("""SELECT a.id, a.label, a.pays_from_account_id, a.pays_externally, o.name AS owner FROM accounts a
                            LEFT JOIN owners o ON o.id = a.owner_id WHERE a.kind = 'card' AND a.active = 1 AND a.issuer != 'sheet'
                            ORDER BY o.name, a.label""").fetchall()
    payers = conn.execute("SELECT id, label FROM accounts WHERE kind IN ('bank','savings') AND active = 1 ORDER BY label").fetchall()
    if cards:
        rows = []
        for c in cards:
            opts = ('<option value="">not set (the app will ask for a matching bank payment)</option>'
                    f'<option value="external"{" selected" if c["pays_externally"] else ""}>an account I do not import (for example a partner\'s Leumi, until I add it)</option>'
                    + "".join(f'<option value="{p["id"]}"{" selected" if c["pays_from_account_id"] == p["id"] else ""}>{esc(p["label"])}</option>' for p in payers))
            rows.append(_form(ctx, "/setup/card-payer", f'<input type="hidden" name="card" value="{c["id"]}"><b dir="auto">{esc(c["label"])}</b> '
                              f'<span class="muted">{esc(c["owner"] or "")}</span> paid from <select name="payer">{opts}</select> <button class="quiet">Save</button>', "row"))
        out.append('<section class="card" style="margin-top:12px"><h2>Which account pays each card</h2><p class="muted">The app matches each card '
                   'statement to a bank payment. Say where each card is paid from, so it only looks in the right account.</p>' + "".join(rows) + "</section>")
    folder = watchfolder.get(conn)
    out.append('<section class="card" style="margin-top:12px"><h2>Statements from a folder (Google Drive)</h2>'
               '<p class="muted">Install Google Drive for desktop, save your statements into one Drive folder (from your phone too), '
               'and choose that folder here. The app picks up new files by itself and ticks the checklist. Files stay in Drive; '
               'only statements go through there, never your data folder.</p>'
               + (f'<p>Watching <code>{esc(folder)}</code></p><p class="muted">{esc(watchfolder.last_result(conn))}</p>'
                  + _form(ctx, "/watch/check", "<button>Check it now</button>", "inline") + " "
                  + _form(ctx, "/watch/clear", '<button class="quiet">Stop watching</button>', "inline") if folder else "")
               + _form(ctx, "/watch/set", f'<input name="folder" style="width:100%;max-width:520px" placeholder="G:\\My Drive\\Statements" '
                       f'value="{esc(folder or "")}" required> <button>{"Change folder" if folder else "Watch this folder"}</button>', "row") + "</section>")
    shortcut = (_form(ctx, "/setup/shortcut", "<button>Put an icon on my desktop</button>", "inline") if ctx.can_shortcut else "")
    out.append('<section class="card" style="margin-top:12px"><h2>This app</h2>'
               f'<p>Your data lives in <code>{esc(ctx.data_root)}</code> on this computer. Statements you drop in are filed under <code>archive</code> there, and a backup is made before every import.</p>'
               f'<p><a href="/history">Import the old budget sheet</a></p><p>{shortcut}</p></section>')
    return layout(conn, ctx, "/setup", "Setup", "".join(out))
