"""Transactions: every payment line, searchable, grouped by day, with its category and whether you confirmed it."""
import re
import sqlite3
from html import escape as esc
from urllib.parse import urlencode

from .charts import money
from .views import Ctx, dmy, layout
from .views_overview import _cat_options, _change_form, month_label

PAGE = 200
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _num(text: str):
    t = text.strip().replace(",", "").replace("₪", "").lstrip("+-−")
    return float(t) if re.fullmatch(r"\d+(\.\d+)?", t) else None


def _day_label(iso: str) -> str:
    from datetime import date
    d = date.fromisoformat(iso)
    return f"{DAYS[d.weekday()]} {d.day} {month_label(iso[:7])}".replace(f" {d.year}", f" {d.year}")


def _signed(v: float, cur: str) -> str:
    s = f"{abs(v):,.2f}"
    return ("+" if v > 0 else "−") + s + ("" if cur == "ILS" else f" {cur}")


def transactions_page(conn: sqlite3.Connection, ctx: Ctx, q: dict) -> str:
    months = [r[0] for r in conn.execute("SELECT DISTINCT budget_month FROM transactions ORDER BY budget_month DESC")]
    if not months:
        return layout(conn, ctx, "/transactions", "Transactions",
                      '<div class="pagehead"><h1>Transactions</h1></div><div class="card"><p>No payments yet. Drop your statements on the <a href="/upload">Add statements</a> page.</p></div>')
    month = q.get("m") or months[0]
    if month != "all" and month not in months:
        month = months[0]
    text = (q.get("q") or "").strip()
    acct = q.get("acct") or ""
    cat = q.get("cat") or ""
    st = q.get("st") if q.get("st") in ("confirmed", "guessed") else "all"
    try:
        limit = max(PAGE, min(5000, int(q.get("n") or PAGE)))
    except ValueError:
        limit = PAGE
    where, args = [], []
    if month != "all":
        where.append("t.budget_month = ?"); args.append(month)
    if acct.isdigit():
        where.append("t.account_id = ?"); args.append(int(acct))
    if cat:
        where.append("c.name = ?"); args.append(cat)
    if st != "all":
        where.append("t.category_status = ?"); args.append("approved" if st == "confirmed" else "proposed")
    ck = q.get("ck") if q.get("ck") in ("todo", "ok", "question") else ""
    if ck:
        from . import checks
        where.append(checks.SPEND)
        where.append("t.check_state IS NULL" if ck == "todo" else "t.check_state = ?")
        if ck != "todo":
            args.append(ck)
    if text:
        n = _num(text)
        like = f"%{text}%"
        if n is not None:
            where.append("(t.description LIKE ? OR t.detail LIKE ? OR ABS(ABS(t.amount) - ?) < 0.005)"); args += [like, like, n]
        else:
            where.append("(t.description LIKE ? OR t.detail LIKE ?)"); args += [like, like]
    sql = ("""SELECT t.id, t.txn_date, t.budget_month, t.description, t.detail, t.amount, t.currency, t.category_status, t.left_out,
                     t.instalment_no, t.instalment_total, t.check_state, t.check_reason, c.name AS cat, a.label AS account
              FROM transactions t JOIN categories c ON c.id = t.category_id JOIN accounts a ON a.id = t.account_id"""
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY t.txn_date DESC, t.id DESC LIMIT ?")
    rows = conn.execute(sql, args + [limit + 1]).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]

    def opt(value, label, cur):
        return f'<option value="{esc(str(value))}"{" selected" if str(value) == str(cur) else ""}>{esc(label)}</option>'

    accounts = conn.execute("SELECT id, label FROM accounts WHERE active = 1 OR id IN (SELECT DISTINCT account_id FROM transactions) ORDER BY label").fetchall()
    cats = [r[0] for r in conn.execute("SELECT name FROM categories ORDER BY kind, name")]
    form = ('<form method="get" action="/transactions" class="tfilter" role="search">'
            f'<input type="search" name="q" value="{esc(text)}" placeholder="Search merchant, note or amount" aria-label="Search">'
            '<select name="m" aria-label="Month">' + opt("all", "All time", month) + "".join(opt(m, month_label(m), month) for m in months[:60]) + "</select>"
            '<select name="acct" aria-label="Account">' + opt("", "All accounts", acct) + "".join(opt(a["id"], a["label"], acct) for a in accounts) + "</select>"
            '<select name="cat" aria-label="Category">' + opt("", "All categories", cat) + "".join(opt(c, c, cat) for c in cats) + "</select>"
            '<select name="st" aria-label="Status">' + opt("all", "All", st) + opt("confirmed", "Confirmed", st) + opt("guessed", "Guessed", st) + "</select>"
            '<select name="ck" aria-label="Expense check">' + opt("", "Any check", ck) + opt("todo", "Not checked", ck) + opt("ok", "Checked", ck) + opt("question", "Flagged", ck) + "</select>"
            "<button>Show</button></form>")

    counted = [r for r in rows if not r["left_out"]]
    inn = sum(r["amount"] for r in counted if r["amount"] > 0 and r["currency"] == "ILS")
    out = sum(r["amount"] for r in counted if r["amount"] < 0 and r["currency"] == "ILS")
    summ = (f'<div class="tsum"><span><span class="mono">{len(rows)}{"+" if more else ""}</span> lines</span>'
            f'<span>In <span class="mono good">{"+" if inn else ""}{inn:,.2f}</span></span>'
            f'<span>Out <span class="mono">{"−" if out else ""}{abs(out):,.2f}</span></span>'
            '<span class="tkey"><span><i class="mk ok"></i>confirmed</span><span><i class="mk guess"></i>guessed</span></span></div>')

    back = "/transactions?" + urlencode({k: v for k, v in q.items() if k in ("q", "m", "acct", "cat", "st", "ck", "n") and v})
    cat_opts = _cat_options(conn)
    body, day, day_rows = [], None, []

    def flush():
        if day is None:
            return
        total = sum(r["amount"] for r in day_rows if not r["left_out"] and r["currency"] == "ILS")
        body.append(f'<div class="tday"><span>{esc(_day_label(day))}</span><span class="mono">{_signed(total, "ILS") if total else "—"}</span></div>'
                    + "".join(_line(ctx, r, cat_opts, back) for r in day_rows))

    for r in rows:
        if r["txn_date"] != day:
            flush()
            day, day_rows = r["txn_date"], []
        day_rows.append(r)
    flush()
    if not rows:
        body.append('<p class="cap" style="padding:16px 20px">Nothing matches these filters.</p>')
    if more:
        nxt = urlencode({**{k: v for k, v in q.items() if k in ("q", "m", "acct", "cat", "st", "ck") and v}, "n": limit + PAGE})
        body.append(f'<div class="tmore"><a href="/transactions?{nxt}">Show more</a></div>')
    head = '<div class="pagehead"><h1>Transactions</h1></div>'
    table = ('<div class="tbl"><div class="thead"><span></span><span>Merchant</span><span>Category</span><span>Account</span><span class="r">Amount</span></div>'
             + "".join(body) + "</div>")
    return layout(conn, ctx, "/transactions", "Transactions", f"{head}{form}{summ}{table}")


def _line(ctx: Ctx, r, cat_opts: str, back: str) -> str:
    ok = r["category_status"] == "approved"
    note = []
    if r["detail"]:
        note.append(esc(r["detail"]))
    if r["instalment_no"] and r["instalment_total"]:
        note.append(f'instalment {r["instalment_no"]} of {r["instalment_total"]}')
    if r["budget_month"] != r["txn_date"][:7]:
        note.append(f'counted in {month_label(r["budget_month"])}')
    if r["check_state"] == "ok":
        note.append("✓ checked")
    elif r["check_state"] == "question":
        note.append("flagged: not recognised")
    if r["left_out"]:
        note.append("left out of the totals")
    cat = r["cat"]
    if cat == "Uncategorised":
        cat_html = '<span class="nocat">No category yet</span>'
    else:
        cat_html = f'<span class="{"" if ok else "gchip"}" dir="auto">{esc(cat)}</span>'
    amt_cls = "mono r " + ("good" if r["amount"] > 0 else "")
    change = _change_form(ctx, r["id"], cat_opts, back, r["budget_month"]) if not ctx.readonly else ""
    return (f'<details class="trow{" lo" if r["left_out"] else ""}"><summary>'
            f'<i class="mk {"ok" if ok else "guess"}" aria-label="{"Confirmed" if ok else "Guessed"}"></i>'
            f'<span class="mer"><bdi dir="auto">{esc(r["description"])}</bdi>' + (f'<span class="cap">{" · ".join(note)}</span>' if note else "") + "</span>"
            f'{cat_html}<span class="acc">{esc(r["account"])}</span><span class="{amt_cls}">{_signed(r["amount"], r["currency"])}</span></summary>'
            f'<div class="tchg">{change}</div></details>')
