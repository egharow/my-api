"""Fetch cards: card logins (encrypted on this computer) and fetching your card lines straight from the sites."""
import time
from html import escape as esc

from . import cardfetch, charts, vault
from .views import Ctx, _form, layout


def _when(ts) -> str:
    return time.strftime("%H:%M", time.localtime(ts)) if ts else ""


def _job_card(job: dict) -> str:
    if job["running"]:
        done = "".join(f'<li>{"✔" if r["ok"] else "✖"} {esc(r.get("name", ""))}: {esc(r["message"])}</li>' for r in job["results"])
        return (f'<section class="card" id="job" style="margin-bottom:12px"><h2>Working…</h2><p>Now {esc(job["step"])}. '
                f'This page refreshes by itself. You can keep using the app meanwhile.</p>{"<ul>" + done + "</ul>" if done else ""}</section>')
    if not job["finished"]:
        return ""
    rows = "".join(f'<li>{"✔" if r["ok"] else "✖"} {esc(r.get("name", ""))}: {esc(r["message"])}</li>' for r in job["results"])
    link = ' <a href="/review">Go to Review</a>' if "Review" in job["summary"] else ""
    return (f'<section class="card" id="job" style="margin-bottom:12px"><h2>Last {"install" if job["kind"] == "install" else "fetch"} '
            f'({_when(job["finished"])})</h2><p>{esc(job["summary"])}{link}</p>{"<ul>" + rows + "</ul>" if rows and job["kind"] == "fetch" else ""}</section>')


def _step(ok: bool, text: str) -> str:
    return f'<li>{"✔" if ok else "◻"} {text}</li>'


def _setup_card(ctx: Ctx, st: dict, busy: bool) -> str:
    steps = [
        _step(st["windows"] and st["vault"], "Windows computer (logins are encrypted by Windows for your user)"
              if st["windows"] else "<b>Works only on the Windows computer the app runs on.</b>"),
        _step(st["node_ok"], f'Node.js {esc(st["node_version"] or "")} found' if st["node_ok"] else
              ('Install <b>Node.js</b> (the "LTS" button on <a href="https://nodejs.org" target="_blank" rel="noopener">nodejs.org</a>, the usual Windows installer), '
               'then Quit and reopen this app.' + (f' (Found version {esc(st["node_version"])}, which is too old.)' if st["node_version"] else ""))),
        _step(bool(st["browser"]), "Chrome or Edge found (used to open the card sites; nothing extra is downloaded)"
              if st["browser"] else "Chrome or Microsoft Edge was not found. Edge comes with Windows; install Chrome or Edge."),
    ]
    if st["ready"]:
        steps.append(_step(True, f'Card scraper {esc(st["installed"])} installed (exact versions, checked against the app\'s lockfile)'))
        btn = ""
    else:
        what = f'Update the card scraper to {esc(st["pinned"])}' if st["update_waiting"] else f'Install the card scraper ({esc(st["pinned"])})'
        steps.append(_step(False, what + " (a minute or two; nothing runs during the install)"))
        can = st["node_ok"] and st["npm"] and not busy
        btn = _form(ctx, "/cards/install", f'<button{"" if can else " disabled"}>{"Update" if st["update_waiting"] else "Install"} the card scraper</button>', "inline")
    return f'<section class="card" style="margin-bottom:12px"><h2>Set up (once)</h2><ol class="plain">{"".join(steps)}</ol>{btn}</section>'


def _logins_card(ctx: Ctx, logins: list, status: dict, ready: bool, busy: bool, pin: bool) -> str:
    if not logins:
        return ('<section class="card" style="margin-bottom:12px"><h2>Saved logins</h2>'
                '<p class="muted">None yet. Add one below for each card company you use.</p></section>')
    pin_in = '<input name="pin" type="password" placeholder="Fetch PIN" autocomplete="off" required size="10"> ' if pin else ""
    rows = ""
    for e in logins:
        s = status.get(e["id"]) or {}
        last = (f'{"✔" if s.get("ok") else "✖"} {esc(s.get("at", "")[:16].replace("T", " "))}<div class="cap">{esc(s.get("message", ""))}</div>'
                if s else '<span class="muted">never fetched</span>')
        acts = (_form(ctx, "/cards/fetch", f'<input type="hidden" name="only" value="{esc(e["id"])}">{pin_in}'
                      f'<button class="quiet"{"" if ready and not busy else " disabled"}>Fetch only this</button>', "inline")
                + " " + _form(ctx, "/cards/fetch", f'<input type="hidden" name="only" value="{esc(e["id"])}"><input type="hidden" name="show" value="1">{pin_in}'
                              f'<button class="quiet" title="Opens the card site in a window so you can watch, and type an SMS code if it asks"'
                              f'{"" if ready and not busy else " disabled"}>Fetch with the window open</button>', "inline")
                + f' <a class="btnlink quiet" href="/cards?edit={esc(e["id"])}#add">Change</a> '
                + _form(ctx, "/cards/forget", f'<input type="hidden" name="id" value="{esc(e["id"])}"><button class="quiet">Forget</button>', "inline"))
        rows += (f'<tr><td><b>{esc(cardfetch.COMPANIES.get(e["company"], {}).get("name", e["company"]))}</b></td><td>{esc(e["person"])}</td>'
                 f'<td>{esc(e.get("saved_at", ""))}</td><td>{last}</td><td>{acts}</td></tr>')
    fetch_all = _form(ctx, "/cards/fetch", f'{pin_in}<button{"" if ready and not busy else " disabled"}>Fetch all cards now</button>', "inline")
    return ('<section class="card" style="margin-bottom:12px" id="fetch"><h2>Saved logins</h2>'
            '<p class="muted">Only which company and whose login are shown. The details themselves are never shown again, not even to you. '
            'If a company fails, try <b>Fetch with the window open</b>: you watch the site log in, which also shows where it gets stuck.</p>'
            + charts.scroll('<table><thead><tr><th>Card company</th><th>Whose</th><th>Saved</th><th>Last fetch</th><th></th></tr></thead>'
                            f'<tbody>{rows}</tbody></table>') + f'<p>{fetch_all}</p></section>')


ADD_JS = """<script>(function(){var s=document.getElementById('co');if(!s)return;function show(){var v=s.value;
document.querySelectorAll('[data-co]').forEach(function(d){var on=d.getAttribute('data-co')===v;d.hidden=!on;
d.querySelectorAll('input').forEach(function(i){i.required=on;i.disabled=!on;});});}s.addEventListener('change',show);show();})();</script>"""


def _add_card(ctx: Ctx, edit: str, pin: bool) -> str:
    company, person = (edit.split(":", 1) + [""])[:2] if edit else ("", ctx.who)
    co_opts = "".join(f'<option value="{k}"{" selected" if k == company else ""}>{esc(v["name"])}</option>' for k, v in cardfetch.COMPANIES.items())
    who_opts = "".join(f'<option{" selected" if o == person else ""}>{esc(o)}</option>' for o in ctx.owners) or "<option>me</option>"
    groups = ""
    for key, c in cardfetch.COMPANIES.items():
        inputs = "".join(
            f'<label class="fld">{esc(label)}<input name="f_{key}_{name}" type="{"password" if name in ("password", "id", "card6Digits") else "text"}" '
            f'autocomplete="{"new-password" if name == "password" else "off"}" spellcheck="false"'
            f'{" inputmode=numeric" if name in ("id", "card6Digits") else ""}></label>'
            for name, label in c["fields"])
        groups += f'<div data-co="{key}">{inputs}</div>'
    pin_in = '<label class="fld">Fetch PIN<input name="pin" type="password" autocomplete="off" required></label>' if pin else ""
    title = "Change a login" if edit else "Add a login"
    inner = (f'<label class="fld">Card company<select id="co" name="company">{co_opts}</select></label>'
             f'<label class="fld">Whose login<select name="person">{who_opts}</select></label>{groups}{pin_in}'
             '<p><button>Save, encrypted on this computer</button></p>')
    return (f'<section class="card" style="margin-bottom:12px" id="add"><h2>{title}</h2>'
            '<p class="muted">Use the same details you type on the card company\'s site. They go straight into Windows\' encryption on this computer: '
            'not into the app folder, my-data, Google Drive or any backup, and never anywhere online except the card company\'s own site when you fetch. '
            'For Shir\'s cards, Shir types her own details.</p>'
            + _form(ctx, "/cards/save", inner, "stack") + ADD_JS + "</section>")


def _pin_card(ctx: Ctx, pin: bool) -> str:
    if pin:
        inner = ('<label class="fld">Current PIN<input name="old" type="password" autocomplete="off" required></label>'
                 '<label class="fld">New PIN (leave empty to turn the PIN off)<input name="new" type="password" autocomplete="off"></label>'
                 '<button class="quiet">Change PIN</button>')
        say = "Fetch PIN is <b>on</b>: it is needed to fetch and to save a login."
    else:
        inner = ('<label class="fld">New PIN (at least 4 characters)<input name="new" type="password" autocomplete="off" required minlength="4"></label>'
                 '<button class="quiet">Turn on the fetch PIN</button>')
        say = "Optional extra lock: with a PIN, even a program running as you cannot open the saved logins without it. You type it each time you fetch."
    return (f'<section class="card" style="margin-bottom:12px"><h2>Fetch PIN</h2><p class="muted">{say} '
            'If you forget it, use Forget all logins and save them again.</p>' + _form(ctx, "/cards/pin", inner, "stack") + "</section>")


def _forget_all_card(ctx: Ctx) -> str:
    inner = ('<label><input type="checkbox" name="sure" value="1" required> Yes, delete every saved card login from this computer</label> '
             '<button class="quiet">Forget all logins</button>')
    return ('<section class="card" style="margin-bottom:12px"><h2>Forget all logins</h2><p class="muted">Deletes the encrypted file. '
            'Lines already brought in stay. Do this before giving away or repairing the computer.</p>' + _form(ctx, "/cards/forget-all", inner, "stack") + "</section>")


def cards_page(conn, ctx: Ctx, q: dict) -> str:
    job = cardfetch.JOB.snapshot()
    st = cardfetch.setup_status()
    try:
        logins, pin = vault.list_logins(), vault.has_pin()
        broken = ""
    except vault.VaultError as exc:
        logins, pin, broken = [], False, f'<div class="banner">{esc(str(exc))}</div>'
    head = ('<div class="pagehead"><h1>Fetch cards</h1></div>'
            '<p class="muted" style="max-width:760px">Brings in your credit card lines straight from Isracard, American Express, Cal and Max, '
            'instead of downloading statement files. It runs only when you press Fetch, only on this computer, and only reads: '
            'it never pays, transfers or changes anything on the sites. Files still work as before.</p>')
    refresh = "<script>setTimeout(function(){location.replace('/cards')},4000)</script>" if job["running"] else ""
    body = (CSS + refresh + head + broken + _job_card(job) + _setup_card(ctx, st, job["running"])
            + _logins_card(ctx, logins, cardfetch.last_status(), st["ready"], job["running"], pin)
            + (_add_card(ctx, q.get("edit", ""), pin) if st["vault"] else "")
            + (_pin_card(ctx, pin) if st["vault"] else "") + (_forget_all_card(ctx) if logins or vault.path().exists() else ""))
    return layout(conn, ctx, "/cards", "Fetch cards", body)


CSS = ("<style>.stack .fld{display:block;margin:8px 0;max-width:420px}.stack .fld input,.stack .fld select{display:block;width:100%;margin-top:4px}"
       "ol.plain{padding-left:0;list-style:none}ol.plain li{margin:6px 0}</style>")

BLOCKED = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           "<body style='font:16px system-ui;max-width:420px;margin:60px auto;padding:0 16px'><h2>Only on the computer</h2>"
           "<p>Card logins and Fetch cards work only on the computer the app runs on, never from a phone or another device.</p>"
           "<p><a href='/'>Back to the overview</a></p>")
