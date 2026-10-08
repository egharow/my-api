"""Net worth breakdown and spending trends: the two pages for looking at the numbers over time."""
import sqlite3
from html import escape as esc

from . import balances, charts, fx, summary
from .charts import money
from .normalize import merchant_key
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
        lines = sorted(groups[kind], key=lambda l: (l["order"], -abs(l["value"])) if l["order"] else (0, -abs(l["value"])))
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
    view = q.get("view") if q.get("view") in ("both", "spending", "income") else "both"
    src_pick = q.get("src") or None
    with_spend = [m for m in summary.months_available(conn) if summary.spending_by_category(conn, m, owner)]
    all_inc = summary.income_by_source(conn, summary.months_available(conn), owner)
    if not with_spend and not all_inc:
        return '<div class="card"><p>No spending to show yet.</p></div>'
    window = _month_window(sorted(set(with_spend) | set(all_inc)) if view != "spending" else with_spend, 240 if rng == "all" else int(rng))
    inc = {m: d for m, d in all_inc.items() if m in window}
    inc_tot = {m: sum(d.values()) for m, d in inc.items()}
    data: dict[str, dict[str, float]] = {}
    for m in window:
        for r in summary.spending_by_category(conn, m, owner):
            data.setdefault(m, {})
            data[m][r["category"]] = data[m].get(r["category"], 0) + r["spent"]
    have = [m for m in window if m in data]
    if not have and view == "spending":
        return '<div class="card"><p>No spending to show yet.</p></div>'
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
    inc_all = sum(inc_tot.values())
    both_months = [m for m in window if m in inc_tot and m in totals]
    kept = sum(inc_tot[m] - totals[m] for m in both_months)
    if view == "income":
        tiles = ('<div class="tiles">' + _tile("Total income", money(inc_all), esc(f"{len(inc_tot)} months"))
                 + _tile("Average a month", money(inc_all / len(inc_tot)) if inc_tot else "–", "")
                 + _tile("Latest month", money(inc_tot[max(inc_tot)]) if inc_tot else "–", esc(max(inc_tot) if inc_tot else "")) + "</div>")
    else:
        tiles = ""
    tiles += ('<div class="tiles">' + _tile("Total spent", money(grand), esc(f"{len(have)} months"))
             + _tile("Average a month", money(avg), "")
             + _tile("Highest month", money(totals[top_m]) if top_m else "–", esc(top_m or ""))
             + _tile("Latest month", money(totals[have[-1]]) if have else "–", esc(have[-1] if have else ""))
             + (_tile("Income", money(inc_all), "") + _tile("Left over", money(kept), esc(f"over {len(both_months)} months with both")) if view == "both" else "")
             + "</div>") if view != "income" else tiles

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
    keep = {"owner": owner, "view": view}
    controls = (f'<p>{_switch(base, view, [("both", "Income and spending"), ("spending", "Spending only"), ("income", "Income only")], "view", {"range": rng, "owner": owner})}'
                f' &nbsp;·&nbsp; {_switch(base, rng, RANGES, "range", keep)} &nbsp;·&nbsp; '
                f'{_switch(base, owner or "", [("", "Household")] + [(o, o) for o in owners], "owner", {"range": rng, "view": view})}</p>')
    inc_note = ('<p class="muted">Income by person follows the name of the source, for example “Salary (Shir)”. '
                'Income only appears for people whose bank account has been imported.</p>')

    inc_total_by_src: dict[str, float] = {}
    for d in inc.values():
        for k, v in d.items():
            inc_total_by_src[k] = inc_total_by_src.get(k, 0) + v
    inc_ranked = sorted(inc_total_by_src, key=lambda k: -inc_total_by_src[k])
    owner_q = "&owner=" + esc(owner) if owner else ""
    if src_pick and src_pick in inc_total_by_src and view != "spending":
        ipick = q.get("month") if q.get("month") in inc else None
        ilines = summary.income_lines(conn, src_pick, [ipick] if ipick else sorted(inc))
        chips = " ".join(f'<a href="{base}?range={rng}&view={view}&src={esc(src_pick)}&month={m}{owner_q}"{" style=font-weight:700" if m == ipick else ""}>{esc(m[2:])}</a>'
                         for m in sorted(inc) if src_pick in inc[m])
        irows = "".join(f'<tr><td>{esc(l["txn_date"])}</td><td dir="auto">{esc(l["description"])}</td><td dir="auto" class="muted">{esc(l["account"])}</td>'
                        f'<td class="n">{l["spent"]:,.2f}</td></tr>' for l in ilines[:400])
        series_i = [(m, inc[m].get(src_pick, 0.0)) for m in sorted(inc)]
        main = (f'<section class="card"><h2>{esc(src_pick)} by month <small><a href="{base}?range={rng}&view={view}{owner_q}">back to all</a></small></h2>'
                f'{charts.bar_chart(series_i, "ILS", src_pick + " by month")}'
                f'<p class="muted">Total {esc(money(inc_total_by_src[src_pick]))}.</p><p>Look at one month: {chips} · '
                f'<a href="{base}?range={rng}&view={view}&src={esc(src_pick)}{owner_q}">all months</a></p></section>'
                f'<section class="card" style="margin-top:16px"><h2>Every payment ({len(ilines)})</h2><div style="overflow-x:auto"><table><thead><tr><th>Date</th>'
                f'<th>Description</th><th>Account</th><th class="n">Amount ₪</th></tr></thead><tbody>{irows}</tbody></table></div></section>')
        small = ""
    elif cat in cat_total and view != "income":
        pick = q.get("month") if q.get("month") in have else None
        lines = summary.category_lines(conn, cat, [pick] if pick else have, owner)
        owner_q = "&owner=" + esc(owner) if owner else ""
        month_chips = " ".join(
            f'<a href="{base}?range={rng}&cat={esc(cat)}&month={m}{owner_q}"{" style=font-weight:700" if m == pick else ""}>{esc(m[2:])}</a>' for m in have)
        by_sub: dict[str, float] = {}
        by_merchant: dict[str, list] = {}
        for l in lines:
            by_sub[l["subcategory"]] = by_sub.get(l["subcategory"], 0) + l["spent"]
            m = by_merchant.setdefault(merchant_key(l["description"]) or l["description"], [0.0, 0, l["description"]])
            m[0] += l["spent"]
            m[1] += 1
        total_shown = sum(l["spent"] for l in lines) or 1
        subs = ""
        if len(by_sub) > 1:
            subs = "<h3>Sub-categories</h3>" + charts.hbars([(k, v, f"{k}: {money(v)}") for k, v in sorted(by_sub.items(), key=lambda kv: -kv[1]) if v > 0])
        top = sorted(by_merchant.items(), key=lambda kv: -kv[1][0])[:15]
        merchants = ('<h3>Biggest merchants</h3>' + charts.hbars([(v[2], v[0], f"{v[1]} payment(s)") for _, v in top if v[0] > 0])) if top else ""
        rows = "".join(
            f'<tr><td>{esc(l["txn_date"])}</td><td dir="auto">{esc(l["description"])}</td><td dir="auto" class="muted">{esc(l["account"])}</td>'
            f'<td>{esc(l["subcategory"])}{" ?" if l["category_status"] == "proposed" else ""}</td><td class="n">{l["spent"]:,.2f}</td></tr>'
            for l in lines[:400])
        more = f'<p class="muted">Showing the newest 400 of {len(lines)}.</p>' if len(lines) > 400 else ""
        scope = f"{esc(pick)} only" if pick else f"all {len(have)} months"
        main = (f'<section class="card"><h2>{esc(cat)} by month <small><a href="{base}?range={rng}{owner_q}">back to all</a></small></h2>'
                f'{charts.bar_chart(series(cat), "ILS", cat + " by month")}'
                f'<p class="muted">Total {esc(money(cat_total[cat]))}, average {esc(money(cat_total[cat] / len(have)))} a month.</p>'
                f'<p>Look at one month: {month_chips} · <a href="{base}?range={rng}&cat={esc(cat)}{owner_q}">all months</a></p></section>'
                f'<section class="card" style="margin-top:16px"><h2>What makes up {esc(cat)} <small>({scope}: {esc(money(total_shown if lines else 0))})</small></h2>'
                f'{subs}{merchants}</section>'
                f'<section class="card" style="margin-top:16px"><h2>Every payment ({len(lines)})</h2>'
                f'<div style="overflow-x:auto"><table><thead><tr><th>Date</th><th>Description</th><th>Account</th><th>Category</th><th class="n">Amount ₪</th></tr></thead>'
                f'<tbody>{rows}</tbody></table></div>{more}'
                f'<p class="muted">A “?” means the category is only proposed so far. Refunds show as negative amounts.</p></section>')
        small = ""
    else:
        months_all = [m for m in window if m in totals or m in inc_tot]
        if view == "both":
            main = (f'<section class="card"><h2>Income and spending by month</h2>'
                    f'{charts.grouped_bars(months_all, [inc_tot.get(m) for m in months_all], [totals.get(m) for m in months_all])}'
                    f'{charts.data_table(["Month", "Income", "Spending", "Left over"], [[m, f"{inc_tot.get(m, 0):,.0f}", f"{totals.get(m, 0):,.0f}", f"{inc_tot.get(m, 0) - totals.get(m, 0):,.0f}"] for m in months_all], "Income and spending by month")}</section>')
        elif view == "income":
            main = (f'<section class="card"><h2>Total income by month</h2>{charts.bar_chart([(m, inc_tot[m]) for m in months_all if m in inc_tot], "ILS", "Income by month")}</section>')
        else:
            main = (f'<section class="card"><h2>Total spending by month</h2>{charts.bar_chart(series(None), "ILS", "Spending by month")}</section>')
        cards = "".join(f'<section class="card"><h2><a href="{base}?range={rng}&cat={esc(c)}{"&owner=" + esc(owner) if owner else ""}">{esc(c)}</a>'
                        f' <small>{esc(money(cat_total[c]))}</small></h2>{charts.bar_chart(series(c), "ILS", c + " by month", average=False)}</section>'
                        for c in ranked[:6])
        small = f'<h2 style="margin-top:16px">Biggest categories over time</h2><div class="grid">{cards}</div>' if view != "income" else ""

    head = "".join(f"<th class='n'>{esc(m[2:])}</th>" for m in have)
    rows = []
    for c in ranked:
        cells = "".join(f'<td class="n"><a href="{base}?range={rng}&cat={esc(c)}&month={m}{"&owner=" + esc(owner) if owner else ""}">{data[m].get(c, 0):,.0f}</a></td>' for m in have)
        rows.append(f'<tr><td dir="auto"><a href="{base}?range={rng}&cat={esc(c)}{"&owner=" + esc(owner) if owner else ""}">{esc(c)}</a></td>{cells}'
                    f'<td class="n"><b>{cat_total[c]:,.0f}</b></td><td class="n">{cat_total[c] / len(have):,.0f}</td></tr>')
    foot = "".join(f'<td class="n"><b>{totals[m]:,.0f}</b></td>' for m in have)
    matrix = (f'<section class="card" style="margin-top:16px"><h2>Every category, every month</h2><div style="overflow-x:auto"><table>'
              f'<thead><tr><th>Category</th>{head}<th class="n">Total</th><th class="n">Avg / month</th></tr></thead><tbody>{"".join(rows)}</tbody>'
              f'<tfoot><tr><td><b>Total</b></td>{foot}<td class="n"><b>{grand:,.0f}</b></td><td class="n"><b>{avg:,.0f}</b></td></tr></tfoot></table></div>'
              f'<p class="muted">Amounts in ₪, by billing month. Card payments and transfers between your own accounts are not counted.</p></section>')
    inc_matrix = ""
    if view != "spending" and inc_ranked and not (src_pick and src_pick in inc_total_by_src):
        ihead = "".join(f"<th class='n'>{esc(m[2:])}</th>" for m in sorted(inc))
        irows = "".join(
            f'<tr><td dir="auto"><a href="{base}?range={rng}&view={view}&src={esc(k)}{owner_q}">{esc(k)}</a></td>'
            + "".join(f'<td class="n"><a href="{base}?range={rng}&view={view}&src={esc(k)}&month={m}{owner_q}">{inc[m].get(k, 0):,.0f}</a></td>' for m in sorted(inc))
            + f'<td class="n"><b>{inc_total_by_src[k]:,.0f}</b></td></tr>' for k in inc_ranked)
        ifoot = "".join(f'<td class="n"><b>{inc_tot[m]:,.0f}</b></td>' for m in sorted(inc))
        inc_matrix = (f'<section class="card" style="margin-top:16px"><h2>Income by source, every month</h2><div style="overflow-x:auto"><table>'
                      f'<thead><tr><th>Source</th>{ihead}<th class="n">Total</th></tr></thead><tbody>{irows}</tbody>'
                      f'<tfoot><tr><td><b>Total</b></td>{ifoot}<td class="n"><b>{inc_all:,.0f}</b></td></tr></tfoot></table></div></section>')
    if view == "income":
        matrix = ""
    html = f'{controls}{tiles}<p>{esc(trend_note) if view != "income" else ""}</p>{source_note if view != "income" else inc_note}{reimb if view != "income" else ""}{main}{small}{inc_matrix}{matrix if not (src_pick and view != "spending") else ""}'
    return html
