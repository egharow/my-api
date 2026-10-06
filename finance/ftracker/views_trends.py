"""Net worth breakdown and spending trends: the two pages for looking at the numbers over time."""
import sqlite3
from html import escape as esc

from . import balances, charts, fx, summary
from .charts import money
from .views import Ctx, _form, _month_window, _tile

KIND_LABEL = {"bank": "Bank accounts", "savings": "Savings", "investment": "Investments", "pension": "Pension & provident funds",
              "real_estate": "Real estate", "loan": "Loans & debts", "other": "Other"}
KIND_ORDER = list(KIND_LABEL)


def _switch(base: str, current: str, options: list[tuple[str, str]], key: str, keep: dict) -> str:
    def href(v):
        params = {**keep, key: v}
        return base + "?" + "&".join(f"{k}={esc(str(x))}" for k, x in params.items() if x)
    return " ".join(f'<a href="{href(v)}"{" style=font-weight:700" if v == current else ""}>{esc(t)}</a>' for v, t in options)


def _filter_panel(conn: sqlite3.Connection, ctx: Ctx, cur: str) -> str:
    """Pick which accounts count toward net worth. Saved, so it applies everywhere net worth is shown."""
    rows = balances.latest(conn, ctx.today)
    if not rows:
        return ""
    off = [r for r in rows if not r["in_wealth"]]
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["kind"], []).append(r)
    body = []
    for kind in sorted(groups, key=lambda k: KIND_ORDER.index(k) if k in KIND_ORDER else 99):
        items = sorted(groups[kind], key=lambda r: (r["owner"] or "", r["label"]))
        all_on = all(r["in_wealth"] for r in items)
        body.append(f'<tr class="grp"><td><input type="checkbox" aria-label="All {esc(KIND_LABEL.get(kind, kind))}" {"checked" if all_on else ""} '
                    f'onchange="document.querySelectorAll(\'.k-{esc(kind)}\').forEach(function(c){{c.checked=this.checked}}.bind(this))"></td>'
                    f'<td colspan="3"><b>{esc(KIND_LABEL.get(kind, kind))}</b></td></tr>')
        for r in items:
            try:
                val = money(fx.convert_asset(conn, r["amount"], r["currency"], cur, r["as_of"]), cur)
            except LookupError:
                val = money(r["amount"], r["currency"])
            body.append(f'<tr><td><input type="checkbox" class="k-{esc(kind)}" name="inc_{r["account_id"]}" value="1" {"checked" if r["in_wealth"] else ""}>'
                        f'<input type="hidden" name="shown" value="{r["account_id"]}"></td>'
                        f'<td dir="auto">{esc(r["label"])}</td><td>{esc(r["owner"] or "")}</td><td class="n">{esc(val)}</td></tr>')
    form = _form(ctx, "/wealth/include",
                 f'<div style="overflow-x:auto"><table><thead><tr><th></th><th>Account</th><th>Owner</th><th class="n">Value ({cur})</th></tr></thead>'
                 f'<tbody>{"".join(body)}</tbody></table></div><p><button>Apply</button> '
                 '<span class="muted">Ticked accounts count toward net worth. This is remembered.</span></p>')
    state = (f'<div class="banner">Not counting {len(off)} account(s): ' + ", ".join(esc(r["label"]) for r in off[:6])
             + (f" and {len(off) - 6} more" if len(off) > 6 else "") + ".</div>") if off else ""
    return f'{state}<details class="card" style="margin-bottom:12px"{" open" if off else ""}><summary><b>Choose what counts in net worth</b></summary>{form}</details>'


def networth_body(conn: sqlite3.Connection, ctx: Ctx, q: dict, base: str = "/wealth") -> str:
    cur = q.get("cur") if q.get("cur") in ("ILS", "USD") else "ILS"
    owner = q.get("owner") or None
    dates = [r[0] for r in conn.execute("SELECT DISTINCT as_of FROM balances ORDER BY as_of")]
    if not dates:
        return '<div class="card"><p>No balances yet. Type them on the <a href="/balances">Balances</a> page.</p></div>'
    try:
        snaps = [balances.net_worth(conn, d, cur, owner) for d in dates]
    except LookupError:
        return ('<div class="banner"><b>No dollar rate yet.</b> '
                '<a href="/balances">Enter it or fetch it on the Balances page</a>.</div>')
    now, prev = snaps[-1], (snaps[-2] if len(snaps) > 1 else None)
    prev_by = {l["account"]: l["value"] for l in prev["lines"]} if prev else {}
    assets = sum(l["value"] for l in now["lines"] if l["value"] > 0)
    debts = sum(l["value"] for l in now["lines"] if l["value"] < 0)
    change = f"{money(now['total'] - prev['total'], cur)} since {prev['as_of']}" if prev else ""
    tiles = ('<div class="tiles">' + _tile("Net worth", money(now["total"], cur), esc(f"as of {now['as_of']}"))
             + _tile("Assets", money(assets, cur), "") + _tile("Debts", money(debts, cur), "")
             + _tile("Change", esc(change) or "–", "") + "</div>")

    groups: dict[str, list] = {}
    for l in now["lines"]:
        groups.setdefault(l["kind"], []).append(l)
    body = []
    for kind in sorted(groups, key=lambda k: KIND_ORDER.index(k) if k in KIND_ORDER else 99):
        lines = sorted(groups[kind], key=lambda l: -abs(l["value"]))
        sub = sum(l["value"] for l in lines)
        body.append(f'<tr class="grp"><td colspan="3"><b>{esc(KIND_LABEL.get(kind, kind))}</b></td><td class="n"></td>'
                    f'<td class="n"><b>{esc(money(sub, cur))}</b></td><td class="n">{abs(sub) / (abs(assets) + abs(debts) or 1) * 100:.0f}%</td><td></td></tr>')
        for l in lines:
            d = l["value"] - prev_by[l["account"]] if l["account"] in prev_by else None
            native = money(l["native"], l["currency"]) if l["currency"] != cur else ""
            body.append(f'<tr><td dir="auto">{esc(l["account"])}</td><td>{esc(l["owner"] or "")}</td><td>{esc(l["as_of"])}</td>'
                        f'<td class="n">{esc(native)}</td><td class="n">{esc(money(l["value"], cur))}</td>'
                        f'<td class="n"></td><td class="n">{esc(("+" if d and d > 0 else "") + money(d, cur)) if d is not None else ""}</td></tr>')
    table = ('<div style="overflow-x:auto"><table><thead><tr><th>Account</th><th>Owner</th><th>Last updated</th><th class="n">Original</th>'
             f'<th class="n">Value ({cur})</th><th class="n">Share</th><th class="n">Change</th></tr></thead><tbody>{"".join(body)}</tbody></table></div>')

    pts = [(s["as_of"], s["total"]) for s in snaps]
    kinds = [k for k in KIND_ORDER if any(l["kind"] == k for s in snaps for l in s["lines"])]
    small = []
    for k in kinds:
        series = [(s["as_of"], sum(l["value"] for l in s["lines"] if l["kind"] == k)) for s in snaps]
        small.append(f'<section class="card"><h2>{esc(KIND_LABEL[k])}</h2>{charts.line_chart(series, cur, KIND_LABEL[k] + " over time")}</section>')
    matrix = charts.data_table(["Date", *[KIND_LABEL[k] for k in kinds], "Total"],
                               [[s["as_of"], *[f'{sum(l["value"] for l in s["lines"] if l["kind"] == k):,.0f}' for k in kinds], f'{s["total"]:,.0f}']
                                for s in snaps], "Net worth by type and date")
    owners = [r[0] for r in conn.execute("SELECT name FROM owners ORDER BY name")]
    keep = {"cur": cur, "owner": owner}
    who = _switch(base, owner or "", [("", "Household")] + [(o, o) for o in owners], "owner", {"cur": cur})
    money_sw = _switch(base, cur, [("ILS", "₪"), ("USD", "$")], "cur", {"owner": owner})
    source = (f'<p class="muted">Where this comes from: the balances you type on the <a href="/balances">Balances</a> page and the ones '
              f'imported from your old sheet ({len(dates)} snapshot dates, {esc(dates[0])} to {esc(dates[-1])}). Each account keeps its last '
              f'typed value until you enter a new one. Dollar accounts use the current rate for every date, so the chart shows how your assets '
              f'moved, not the exchange rate.</p>')
    deltas = [(snaps[i]["as_of"], snaps[i]["total"] - snaps[i - 1]["total"]) for i in range(1, len(snaps))]
    html = (f'<p>{who} &nbsp;·&nbsp; {money_sw}</p>{_filter_panel(conn, ctx, cur)}{tiles}{source}'
            f'<section class="card"><h2>Total over time</h2>{charts.line_chart(pts, cur, "Net worth over time")}</section>'
            f'<section class="card" style="margin-top:16px"><h2>Change between updates</h2>{charts.bar_chart(deltas, cur, "Net worth change", signed=True)}'
            f'<p class="muted">Blue: it grew. Orange: it fell. Each bar is the change since the previous update of your balances.</p></section>'
            f'<section class="card" style="margin-top:16px"><h2>What it is made of</h2>{table}</section>'
            f'<h2 style="margin-top:16px">By type over time</h2><div class="grid">{"".join(small)}</div>{matrix}')
    return html


RANGES = [("6", "6 months"), ("12", "12 months"), ("24", "2 years"), ("all", "All")]


def trends_body(conn: sqlite3.Connection, ctx: Ctx, q: dict, base: str = "/spending") -> str:
    rng = q.get("range") if q.get("range") in {r for r, _ in RANGES} else "all"
    owner = q.get("owner") or None
    cat = q.get("cat") or None
    with_spend = [m for m in summary.months_available(conn) if summary.spending_by_category(conn, m, owner)]
    if not with_spend:
        return '<div class="card"><p>No spending to show yet.</p></div>'
    window = _month_window(with_spend, 240 if rng == "all" else int(rng))
    data: dict[str, dict[str, float]] = {}
    for m in window:
        for r in summary.spending_by_category(conn, m, owner):
            data.setdefault(m, {})
            data[m][r["category"]] = data[m].get(r["category"], 0) + r["spent"]
    have = [m for m in window if m in data]
    totals = {m: sum(data[m].values()) for m in have}
    grand = sum(totals.values())
    avg = grand / len(have) if have else 0
    top_m = max(have, key=lambda m: totals[m]) if have else None
    cat_total: dict[str, float] = {}
    for m in have:
        for c, v in data[m].items():
            cat_total[c] = cat_total.get(c, 0) + v
    ranked = sorted(cat_total, key=lambda c: -cat_total[c])

    half = len(have) // 2
    trend_note = ""
    if half >= 2:
        first, second = sum(totals[m] for m in have[:half]) / half, sum(totals[m] for m in have[half:]) / (len(have) - half)
        pct = (second - first) / first * 100 if first else 0
        trend_note = f"Monthly average {'up' if pct > 0 else 'down'} {abs(pct):.0f}% in the second half of this period ({money(first)} to {money(second)})."
    tiles = ('<div class="tiles">' + _tile("Total spent", money(grand), esc(f"{len(have)} months"))
             + _tile("Average a month", money(avg), "")
             + _tile("Highest month", money(totals[top_m]) if top_m else "–", esc(top_m or ""))
             + _tile("Latest month", money(totals[have[-1]]) if have else "–", esc(have[-1] if have else "")) + "</div>")

    def series(c):
        return [(m, data[m].get(c, 0.0) if c else totals[m]) for m in have]

    src = summary.spending_by_source(conn, have, owner)
    parts = [f"cards {money(src.get('card', 0))}", f"bank statements (mortgage, loans, fees, direct debits) {money(src.get('bank', 0))}"]
    if src.get("sheet"):
        parts.append(f"your old budget sheet {money(src['sheet'])}")
    source_note = f'<p class="muted">Where it was recorded: {esc(" · ".join(parts))}.</p>'
    reimb = ""
    inc_r = summary.include_reimbursed(conn)
    reimb = _form(ctx, "/setting/reimbursed",
                  f'<span>Vituri (paid back in cash) is {"counted in" if inc_r else "left out of"} these numbers.</span>'
                  f'<input type="hidden" name="on" value="{0 if inc_r else 1}"><input type="hidden" name="next" value="{base}">'
                  f'<button class="quiet">{"Leave out" if inc_r else "Include"}</button>', "row")

    owners = [r[0] for r in conn.execute("SELECT name FROM owners ORDER BY name")]
    keep = {"owner": owner}
    controls = (f'<p>{_switch(base, rng, RANGES, "range", keep)} &nbsp;·&nbsp; '
                f'{_switch(base, owner or "", [("", "Household")] + [(o, o) for o in owners], "owner", {"range": rng})}</p>')

    if cat in cat_total:
        main = (f'<section class="card"><h2>{esc(cat)} by month <small><a href="{base}?range={rng}">back to all</a></small></h2>'
                f'{charts.bar_chart(series(cat), "ILS", cat + " by month")}'
                f'<p class="muted">Total {esc(money(cat_total[cat]))}, average {esc(money(cat_total[cat] / len(have)))} a month.</p></section>')
        small = ""
    else:
        main = (f'<section class="card"><h2>Total spending by month</h2>{charts.bar_chart(series(None), "ILS", "Spending by month")}</section>')
        cards = "".join(f'<section class="card"><h2><a href="{base}?range={rng}&cat={esc(c)}{"&owner=" + esc(owner) if owner else ""}">{esc(c)}</a>'
                        f' <small>{esc(money(cat_total[c]))}</small></h2>{charts.bar_chart(series(c), "ILS", c + " by month", average=False)}</section>'
                        for c in ranked[:6])
        small = f'<h2 style="margin-top:16px">Biggest categories over time</h2><div class="grid">{cards}</div>'

    head = "".join(f"<th class='n'>{esc(m[2:])}</th>" for m in have)
    rows = []
    for c in ranked:
        cells = "".join(f'<td class="n">{data[m].get(c, 0):,.0f}</td>' for m in have)
        rows.append(f'<tr><td dir="auto"><a href="{base}?range={rng}&cat={esc(c)}{"&owner=" + esc(owner) if owner else ""}">{esc(c)}</a></td>{cells}'
                    f'<td class="n"><b>{cat_total[c]:,.0f}</b></td><td class="n">{cat_total[c] / len(have):,.0f}</td></tr>')
    foot = "".join(f'<td class="n"><b>{totals[m]:,.0f}</b></td>' for m in have)
    matrix = (f'<section class="card" style="margin-top:16px"><h2>Every category, every month</h2><div style="overflow-x:auto"><table>'
              f'<thead><tr><th>Category</th>{head}<th class="n">Total</th><th class="n">Avg / month</th></tr></thead><tbody>{"".join(rows)}</tbody>'
              f'<tfoot><tr><td><b>Total</b></td>{foot}<td class="n"><b>{grand:,.0f}</b></td><td class="n"><b>{avg:,.0f}</b></td></tr></tfoot></table></div>'
              f'<p class="muted">Amounts in ₪, by billing month. Card payments and transfers between your own accounts are not counted.</p></section>')
    html = f'{controls}{tiles}<p>{esc(trend_note)}</p>{source_note}{reimb}{main}{small}{matrix}'
    return html
