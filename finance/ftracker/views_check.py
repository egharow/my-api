"""Check expenses: go through a month's spending and say that each payment really happened."""
import sqlite3
from html import escape as esc
from urllib.parse import urlencode

from . import checks
from .charts import money
from .views import Ctx, _form, dmy, layout
from .views_overview import month_label, shift

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _day_label(iso: str) -> str:
    from datetime import date
    d = date.fromisoformat(iso)
    return f"{DAYS[d.weekday()]} {d.day} {month_label(iso[:7])}"


def _signed(v: float, cur: str) -> str:
    return ("+" if v > 0 else "−") + f"{abs(v):,.2f}" + ("" if cur == "ILS" else f" {cur}")


def _qs(**kw) -> str:
    return "/check?" + urlencode({k: v for k, v in kw.items() if v not in (None, "")})


def _ok_form(ctx: Ctx, r, back: str, label: str = "✓") -> str:
    return _form(ctx, "/check/mark", f'<input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="state" value="ok">'
                 f'<input type="hidden" name="back" value="{esc(back)}"><input type="hidden" name="m" value="{esc(r["budget_month"])}">'
                 f'<button class="okb" title="It happened" aria-label="It happened">{label}</button>', "inline ajaxok")


def _question_box(ctx: Ctx, r, back: str) -> str:
    reasons = "".join(f'<option value="{k}">{esc(v)}</option>' for k, v in checks.REASONS.items())
    return ('<details class="qbox"><summary class="qb" title="I do not recognise this" aria-label="I do not recognise this">?</summary>'
            + _form(ctx, "/check/mark", f'<input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="state" value="question">'
                    f'<input type="hidden" name="back" value="{esc(back)}"><select name="reason" required>{reasons}</select> '
                    '<input name="note" placeholder="Note (optional)" maxlength="300"> <button>Flag it</button>', "row") + "</details>")


def _row(ctx: Ctx, r, back: str, with_month: bool = False) -> str:
    note = [f'<bdi dir="auto">{esc(r["account"])}</bdi>', f'<bdi dir="auto">{esc(r["cat"])}</bdi>']
    if r["instalment_no"] and r["instalment_total"]:
        note.append(f'instalment {r["instalment_no"]} of {r["instalment_total"]}')
    if with_month or r["budget_month"] != r["txn_date"][:7]:
        note.append(f'counts in {esc(month_label(r["budget_month"]))}')
    if r["detail"]:
        note.append(esc(r["detail"]))
    if r["check_state"] == "ok":
        acts = f'<span class="cap">✓ {esc(r["check_by"] or "")}</span> ' + _form(
            ctx, "/check/mark", f'<input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="state" value="">'
            f'<input type="hidden" name="back" value="{esc(back)}"><button class="quiet">Undo</button>', "inline")
    elif r["check_state"] == "question":
        acts = (f'<span class="flag">{esc(checks.REASONS.get(r["check_reason"], "Flagged"))}</span> ' + _ok_form(ctx, r, back, "It is fine")
                + " " + _form(ctx, "/line/leaveout", f'<input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="on" value="1">'
                              f'<input type="hidden" name="back" value="{esc(back)}"><button class="quiet" title="Keep it in your records but out of the totals">Leave out of totals</button>', "inline")
                + " " + _form(ctx, "/check/mark", f'<input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="state" value="">'
                              f'<input type="hidden" name="back" value="{esc(back)}"><button class="quiet">Back to unchecked</button>', "inline"))
        if r["check_note"]:
            note.append("“" + esc(r["check_note"]) + "”")
        if r["check_by"]:
            note.append(f'flagged by {esc(r["check_by"])}')
    else:
        acts = _ok_form(ctx, r, back) + " " + _question_box(ctx, r, back)
    amt_cls = "mono n" + (" good" if r["amount"] > 0 else "")
    return (f'<div class="ckrow {esc(r["check_state"] or "todo")}" data-id="{r["id"]}" tabindex="-1"><span class="mer"><bdi dir="auto">{esc(r["description"])}</bdi>'
            f'<span class="cap">{" · ".join(note)}</span></span><span class="{amt_cls}">{_signed(r["amount"], r["currency"])}</span>'
            f'<span class="acts">{acts}</span></div>')


CHECK_JS = """(function(){var rows=[].slice.call(document.querySelectorAll('.ckrow.todo'));var cur=-1;
function setCur(i){if(rows[cur])rows[cur].classList.remove('cur');cur=Math.max(0,Math.min(rows.length-1,i));if(rows[cur]){rows[cur].classList.add('cur');rows[cur].scrollIntoView({block:'nearest'})}}
function bump(){var t=document.getElementById('ck-todo');if(t){var n=Math.max(0,parseInt(t.textContent||'0',10)-1);t.textContent=n}var d=document.getElementById('ck-done');if(d)d.textContent=parseInt(d.textContent||'0',10)+1}
document.addEventListener('submit',function(e){var f=e.target;if(!f.classList||!f.classList.contains('ajaxok')||!window.fetch)return;e.preventDefault();
var row=f.closest('.ckrow');fetch(f.action,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','X-Ajax':'1'},body:new URLSearchParams(new FormData(f)).toString()})
.then(function(r){return r.json()}).then(function(j){if(!j.ok){location.reload();return}if(row){row.classList.remove('todo');row.classList.add('ok');row.style.display='none';bump();var i=rows.indexOf(row);if(i>=0){rows.splice(i,1);if(cur>=rows.length)cur=rows.length-1;setCur(cur<0?0:cur)}}})
.catch(function(){f.submit()})});
document.addEventListener('keydown',function(e){var t=e.target&&e.target.tagName;if(t==='INPUT'||t==='SELECT'||t==='TEXTAREA'||e.metaKey||e.ctrlKey||e.altKey||!rows.length)return;
if(e.key==='j'||e.key==='J'){setCur(cur+1)}else if(e.key==='k'||e.key==='K'){setCur(cur-1)}
else if(e.key===' '||e.key==='Enter'){if(cur<0)setCur(0);var b=rows[cur]&&rows[cur].querySelector('.okb');if(b){e.preventDefault();b.click()}}
else if(e.key==='q'||e.key==='Q'){if(cur<0)setCur(0);var d=rows[cur]&&rows[cur].querySelector('.qbox');if(d){e.preventDefault();d.open=true;var s=d.querySelector('select');if(s)s.focus()}}});
if(rows.length)setCur(0)})();"""


def check_page(conn: sqlite3.Connection, ctx: Ctx, q: dict) -> str:
    all_months = checks.months(conn)
    head = '<div class="pagehead"><h1>Check expenses</h1><div class="sub">Did each payment really happen? This is separate from its category.</div></div>'
    if not all_months:
        return layout(conn, ctx, "/check", "Check expenses", head + '<div class="card"><p>No spending to check yet. Import your statements first.</p></div>')
    if q.get("view") == "months":
        rows = "".join(
            f'<tr><td><a href="{_qs(m=m["month"])}">{esc(month_label(m["month"]))}</a></td><td class="n">{m["total"]}</td><td class="n">{m["ok"]}</td>'
            f'<td class="n">{m["question"]}</td><td class="n">{m["todo"]}</td><td>{"✔ all checked" if m["todo"] == 0 and m["question"] == 0 else ""}</td></tr>' for m in all_months)
        body = (f'<div class="card"><p><a href="{_qs()}">← Back to {esc(month_label(all_months[0]["month"]))}</a></p>'
                '<table><thead><tr><th>Month</th><th class="n">Expenses</th><th class="n">Checked</th><th class="n">Flagged</th><th class="n">To check</th><th></th></tr></thead>'
                f'<tbody>{rows}</tbody></table></div>')
        return layout(conn, ctx, "/check", "Check expenses", head + body)

    known = {m["month"] for m in all_months}
    month = q.get("m") if q.get("m") in known else all_months[0]["month"]
    show = q.get("show") if q.get("show") in ("todo", "ok", "all") else "todo"
    sort = "amount" if q.get("sort") == "amount" else "date"
    acct = int(q["acct"]) if (q.get("acct") or "").isdigit() else None
    cat = q.get("cat") or ""
    back = _qs(m=month, show=None if show == "todo" else show, sort=None if sort == "date" else sort, acct=acct, cat=cat)
    c = checks.counts(conn, month)

    out = [head]
    flagged = checks.flagged(conn)
    if flagged:
        out.append(f'<section class="card flagbox" id="flagged"><h2>Flagged · {len(flagged)}</h2>'
                   '<p class="muted">Payments you did not recognise. They stay in your totals until you leave them out. Decide each one.</p>'
                   + "".join(_row(ctx, r, back, with_month=True) for r in flagged) + "</section>")

    prev, nxt = shift(month, -1), shift(month, 1)
    ordered = sorted(known)
    has_prev = any(m < month for m in ordered)
    has_next = any(m > month for m in ordered)
    nav = ((f'<a class="nb" href="{_qs(m=max(m for m in ordered if m < month))}" aria-label="Earlier month">‹</a>' if has_prev else '<span class="nb off">‹</span>')
           + f'<b class="mt">{esc(month_label(month))}</b>'
           + (f'<a class="nb" href="{_qs(m=min(m for m in ordered if m > month))}" aria-label="Later month">›</a>' if has_next else '<span class="nb off">›</span>'))
    done = c["ok"] + c["question"]
    pct = 100 * done / c["total"] if c["total"] else 0
    accts = conn.execute("SELECT DISTINCT a.id, a.label FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.budget_month = ? ORDER BY a.label", (month,)).fetchall()
    cats = [r[0] for r in conn.execute("SELECT DISTINCT c.name FROM transactions t JOIN categories c ON c.id = t.category_id WHERE t.budget_month = ? ORDER BY c.name", (month,))]
    opt = lambda v, label, cur: f'<option value="{esc(str(v))}"{" selected" if str(v) == str(cur) else ""}>{esc(label)}</option>'
    filt = ('<form method="get" action="/check" class="tfilter">'
            f'<input type="hidden" name="m" value="{esc(month)}">'
            '<select name="show" aria-label="Show">' + opt("todo", "Not checked yet", show) + opt("ok", "Checked", show) + opt("all", "Everything", show) + "</select>"
            '<select name="acct" aria-label="Account">' + opt("", "All accounts", acct or "") + "".join(opt(a["id"], a["label"], acct or "") for a in accts) + "</select>"
            '<select name="cat" aria-label="Category">' + opt("", "All categories", cat) + "".join(opt(x, x, cat) for x in cats) + "</select>"
            '<select name="sort" aria-label="Order">' + opt("date", "By date", sort) + opt("amount", "Largest first", sort) + "</select><button>Show</button></form>")
    out.append(f'<div class="modes"><span class="monthnav">{nav}</span><a class="btnlink" href="{_qs(view="months")}">Show earlier months</a></div>')
    out.append(f'<div class="band" role="status"><div class="bar" aria-hidden="true"><span style="width:{pct:.1f}%"></span></div>'
               f'<div><span class="mono" id="ck-done">{done}</span> of <span class="mono">{c["total"]}</span> checked · '
               f'<span class="mono" id="ck-todo">{c["todo"]}</span> to go'
               + (f' · <a href="#flagged">{c["question"]} flagged</a>' if c["question"] else "") + "</div></div>")
    out.append(filt)

    rows = checks.lines(conn, month, show, acct, cat, sort)
    if not rows:
        out.append('<div class="card"><p>' + ("Everything shown is checked. ✔" if show == "todo" else "Nothing matches these filters.") + "</p></div>")
        return layout(conn, ctx, "/check", "Check expenses", "".join(out))
    todo_ids = [r["id"] for r in rows if r["check_state"] is None]
    if todo_ids:
        out.append(_form(ctx, "/check/mark", "".join(f'<input type="hidden" name="id" value="{i}">' for i in todo_ids)
                         + f'<input type="hidden" name="state" value="ok"><input type="hidden" name="back" value="{esc(back)}">'
                         f'<button class="quiet" onclick="return confirm(\'Mark all {len(todo_ids)} shown payments as happened?\')">Mark everything shown as checked ({len(todo_ids)})</button>', "inline"))
    body = []
    if sort == "date":
        day, group = None, []

        def flush():
            if day is None:
                return
            ids = [r["id"] for r in group if r["check_state"] is None]
            btn = (_form(ctx, "/check/mark", "".join(f'<input type="hidden" name="id" value="{i}">' for i in ids)
                         + f'<input type="hidden" name="state" value="ok"><input type="hidden" name="back" value="{esc(back)}"><button class="quiet">Mark this day checked</button>', "inline")
                   if ids else "")
            total = sum(r["amount"] for r in group)
            body.append(f'<div class="ckday"><span>{esc(_day_label(day))}</span><span class="mono">{_signed(total, "ILS")}</span><span>{btn}</span></div>'
                        + "".join(_row(ctx, r, back) for r in group))
        for r in rows:
            if r["txn_date"] != day:
                flush()
                day, group = r["txn_date"], []
            group.append(r)
        flush()
    else:
        body.append("".join(_row(ctx, r, back) for r in rows))
    out.append(f'<div class="cklist">{"".join(body)}</div>')
    out.append('<p class="keys cap"><kbd>J</kbd> <kbd>K</kbd> move · <kbd>Space</kbd> it happened · <kbd>Q</kbd> I do not recognise it</p>')
    out.append(f"<script>{CHECK_JS}</script>")
    return layout(conn, ctx, "/check", "Check expenses", "".join(out))
