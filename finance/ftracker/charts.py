"""Small dependency-free SVG/HTML charts. Colours are CSS variables defined in the page theme."""
import math
from datetime import date
from html import escape

SYMBOL = {"ILS": "₪", "USD": "$"}


def money(v, currency: str = "ILS", decimals: int = 0) -> str:
    if v is None:
        return "–"
    sign = "-" if v < 0 else ""
    return f"{sign}{SYMBOL.get(currency, currency + ' ')}{abs(v):,.{decimals}f}"


def compact(v: float, currency: str = "ILS") -> str:
    a = abs(v)
    s = SYMBOL.get(currency, "")
    sign = "-" if v < 0 else ""
    if a >= 1_000_000:
        return f"{sign}{s}{a / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"{sign}{s}{a / 1_000:.0f}k"
    return f"{sign}{s}{a:.0f}"


def nice_axis(vmax: float, parts: int = 4) -> tuple[float, list[float]]:
    if vmax <= 0:
        return 1.0, [0.0, 1.0]
    raw = vmax / parts
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    top = math.ceil(vmax / step) * step
    return top, [i * step for i in range(int(round(top / step)) + 1)]


def _legend(items: list[tuple[str, str]]) -> str:
    return '<div class="legend">' + "".join(
        f'<span><i style="background:var({var})"></i>{escape(name)}</span>' for name, var in items) + "</div>"


def _round_top(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    if h <= 0:
        return ""
    r = min(r, w / 2, h)
    return (f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z")


def line_chart(points: list[tuple[str, float]], currency: str, label: str) -> str:
    """points: (iso date, value). Time-proportional x axis, zero baseline, direct label on the last point."""
    if not points:
        return '<p class="muted">No data yet.</p>'
    W, H, L, R, T, B = 640, 250, 56, 64, 16, 30
    days = [date.fromisoformat(d).toordinal() for d, _ in points]
    x0, x1 = min(days), max(days)
    top, ticks = nice_axis(max(v for _, v in points))
    floor = min(0.0, min(v for _, v in points))
    if floor < 0:
        top, ticks = nice_axis(max(abs(floor), top))
        floor = -top if abs(floor) > 0 else 0.0
    span = top - floor

    def sx(d):
        return L + (W - L - R) * (0.5 if x1 == x0 else (d - x0) / (x1 - x0))

    def sy(v):
        return T + (H - T - B) * (1 - (v - floor) / span)

    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{escape(label)}" class="chart">']
    for t in ticks:
        y = sy(t)
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end" class="axis">{escape(compact(t, currency))}</text>')
    pts = " ".join(f"{sx(d):.1f},{sy(v):.1f}" for d, (_, v) in zip(days, points))
    out.append(f'<polyline points="{pts}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round"/>')
    xs = [sx(d) for d in days]
    show: list[int] = []
    for i, cx in enumerate(xs):
        if not show or cx - xs[show[-1]] >= 62:
            show.append(i)
    if show[-1] != len(xs) - 1:
        if xs[-1] - xs[show[-1]] >= 62:
            show.append(len(xs) - 1)
        else:
            show[-1] = len(xs) - 1          # keep the newest date labelled; drop the one it would collide with
    for i, (d, (iso, v)) in enumerate(zip(days, points)):
        cx, cy = xs[i], sy(v)
        tip = f"{iso}: {money(v, currency)}"
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="var(--s1)" stroke="var(--surface)" stroke-width="2"/>'
                   f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="14" fill="transparent" data-tip="{escape(tip)}"/>')
        if i in show:
            out.append(f'<text x="{cx:.1f}" y="{H - 8}" text-anchor="middle" class="axis">{escape(iso[:7])}</text>')
    lx, ly = sx(days[-1]), sy(points[-1][1])
    out.append(f'<text x="{lx + 8:.1f}" y="{ly + 4:.1f}" class="direct">{escape(compact(points[-1][1], currency))}</text>')
    out.append("</svg>")
    return "".join(out)


def grouped_bars(months: list[str], income: list[float | None], spending: list[float | None], currency: str = "ILS") -> str:
    if not months:
        return '<p class="muted">No data yet.</p>'
    W, H, L, R, T, B = 640, 250, 56, 12, 16, 30
    top, ticks = nice_axis(max([v for v in income + spending if v is not None], default=0))
    n = len(months)
    gw = (W - L - R) / n
    bw = max(4.0, min(16.0, gw / 2.8))
    base = H - B

    def sy(v):
        return T + (H - T - B) * (1 - v / top)

    out = [_legend([("Income", "--s1"), ("Expenses", "--s2")]),
           f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Income and spending by month" class="chart">']
    for t in ticks:
        y = sy(t)
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end" class="axis">{escape(compact(t, currency))}</text>')
    step = max(1, math.ceil(n / 8))
    for i, m in enumerate(months):
        gx = L + gw * i + gw / 2
        for k, (val, var) in enumerate(((income[i], "--s1"), (spending[i], "--s2"))):
            if val is None:
                continue
            x = gx - bw - 1 + k * (bw + 2)
            out.append(f'<path d="{_round_top(x, sy(val), bw, base - sy(val))}" fill="var({var})"/>')
        tip = (f"{m}: no data" if income[i] is None and spending[i] is None
               else f"{m}: income {money(income[i], currency)}, spending {money(spending[i], currency)}")
        out.append(f'<rect x="{gx - gw / 2:.1f}" y="{T}" width="{gw:.1f}" height="{base - T}" fill="transparent" data-tip="{escape(tip)}"/>')
        if i % step == 0:
            out.append(f'<text x="{gx:.1f}" y="{H - 8}" text-anchor="middle" class="axis">{escape(m[2:])}</text>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{base}" y2="{base}" class="baseline"/></svg>')
    return "".join(out)


def hbars(rows: list[tuple[str, float, str]], currency: str = "ILS") -> str:
    """rows: (label, value, tooltip/detail). One hue, sorted by the caller; values in text ink."""
    if not rows:
        return '<p class="muted">Nothing in this month yet.</p>'
    top = max(v for _, v, _ in rows) or 1
    total = sum(v for _, v, _ in rows) or 1
    items = []
    for label, v, detail in rows:
        items.append(
            f'<div class="hrow" data-tip="{escape(detail)}"><span class="hlabel" dir="auto">{escape(label)}</span>'
            f'<span class="htrack"><span class="hbar" style="width:{max(v / top * 100, 0.6):.1f}%"></span></span>'
            f'<span class="hval">{escape(money(v, currency))}<small>{v / total * 100:.0f}%</small></span></div>')
    return '<div class="hbars">' + "".join(items) + "</div>"


def progress_bar(fraction: float | None, status: str) -> str:
    pct = 0 if fraction is None else max(0, min(fraction, 1)) * 100
    return (f'<div class="pbar" role="progressbar" aria-valuenow="{pct:.0f}" aria-valuemin="0" aria-valuemax="100">'
            f'<span class="pfill {escape(status)}" style="width:{pct:.1f}%"></span></div>')


STATUS = {
    "on_track": ("✔", "On track", "good"),
    "reached": ("✔", "Reached", "good"),
    "behind": ("▲", "Behind", "serious"),
    "tracking": ("•", "Tracking", "muted"),
    "no_data": ("?", "Needs more balances", "muted"),
    "needs_fx": ("!", "Needs a USD rate", "warning"),
}


def status_chip(status: str) -> str:
    icon, text, tone = STATUS.get(status, ("•", status, "muted"))
    return f'<span class="chip {tone}"><b aria-hidden="true">{icon}</b> {escape(text)}</span>'


def scroll(html: str) -> str:
    """Wide tables scroll inside their card instead of widening the page on a phone."""
    return f'<div style="overflow-x:auto">{html}</div>'


def data_table(header: list[str], rows: list[list], caption: str) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return (f'<details class="tableview"><summary>Table view</summary><table><caption>{escape(caption)}</caption>'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></details>")


def bar_chart(points: list[tuple[str, float]], currency: str, label: str, signed: bool = False, average: bool = True) -> str:
    """One bar per period. points: (label like '2026-03', value). With signed=True, falls are drawn in the second hue
    below a zero line (used for 'how much did it change')."""
    if not points:
        return '<p class="muted">No data yet.</p>'
    W, H, L, R, T, B = 640, 250, 56, 12, 16, 30
    vals = [v for _, v in points]
    hi, lo = max(max(vals), 0.0), min(min(vals), 0.0) if signed else 0.0
    top, _ = nice_axis(max(hi, abs(lo)) or 1)
    floor = -top if lo < 0 else 0.0
    ticks = [floor + i * (top - floor) / 4 for i in range(5)]
    n = len(points)
    gw = (W - L - R) / n
    bw = max(3.0, min(34.0, gw * 0.7))

    def sy(v):
        return T + (H - T - B) * (1 - (v - floor) / (top - floor))

    zero = sy(0.0)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{escape(label)}" class="chart">']
    for t in ticks:
        y = sy(t)
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end" class="axis">{escape(compact(t, currency))}</text>')
    step = max(1, math.ceil(n / 9))
    for i, (lab, v) in enumerate(points):
        cx = L + gw * i + gw / 2
        y0, y1 = sorted((zero, sy(v)))
        colour = "--s2" if (signed and v < 0) else "--s1"
        if y1 - y0 >= 0.5:
            out.append(f'<rect x="{cx - bw / 2:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{y1 - y0:.1f}" rx="3" fill="var({colour})"/>')
        out.append(f'<rect x="{cx - gw / 2:.1f}" y="{T}" width="{gw:.1f}" height="{H - T - B}" fill="transparent" data-tip="{escape(f"{lab}: {money(v, currency)}")}"/>')
        if i % step == 0 or i == n - 1 and (n - 1) % step >= step // 2 + 1:
            out.append(f'<text x="{cx:.1f}" y="{H - 8}" text-anchor="middle" class="axis">{escape(lab[2:] if len(lab) == 7 else lab)}</text>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{zero:.1f}" y2="{zero:.1f}" class="baseline"/>')
    if average and n > 1 and not signed:
        avg = sum(vals) / n
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(avg):.1f}" y2="{sy(avg):.1f}" stroke="var(--ink2)" stroke-dasharray="5 4" stroke-width="1.5"/>'
                   f'<text x="{W - R}" y="{sy(avg) - 5:.1f}" text-anchor="end" class="axis">average {escape(compact(avg, currency))}</text>')
    out.append("</svg>")
    return "".join(out)


OTHER = "Other"


def _color(i: int, name: str) -> str:
    return "--cx" if name == OTHER else f"--c{i % 6 + 1}"


def _swatches(names: list[str]) -> str:
    return _legend([(n, _color(i, n)) for i, n in enumerate(names)])


def stacked_bars(months: list[str], series: list[tuple[str, list[float]]], currency: str, label: str) -> str:
    """One column per month, split into parts that add up to the month's total. series: (name, value per month)."""
    if not months or not series:
        return '<p class="muted">No data yet.</p>'
    W, H, L, R, T, B = 640, 260, 56, 12, 16, 30
    totals = [sum(v[i] for _, v in series) for i in range(len(months))]
    top, ticks = nice_axis(max(totals) or 1)
    n = len(months)
    gw = (W - L - R) / n
    bw = max(8.0, min(40.0, gw * 0.62))
    base = H - B

    def sy(v):
        return T + (H - T - B) * (1 - v / top)

    out = [_swatches([nme for nme, _ in series]), f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{escape(label)}" class="chart">']
    for t in ticks:
        y = sy(t)
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end" class="axis">{escape(compact(t, currency))}</text>')
    step = max(1, math.ceil(n / 8))
    for i, m in enumerate(months):
        gx = L + gw * i + gw / 2
        acc = 0.0
        for k, (name, vals) in enumerate(series):
            v = vals[i]
            if v <= 0:
                continue
            y1, y0 = sy(acc + v), sy(acc)
            out.append(f'<rect x="{gx - bw / 2:.1f}" y="{y1:.1f}" width="{bw:.1f}" height="{max(y0 - y1, 0.5):.1f}" fill="var({_color(k, name)})" '
                       f'stroke="var(--surface)" stroke-width="1"/>')
            acc += v
        parts = ", ".join(f"{nme} {money(v[i], currency)}" for nme, v in series if v[i] > 0)
        out.append(f'<rect x="{gx - gw / 2:.1f}" y="{T}" width="{gw:.1f}" height="{base - T}" fill="transparent" '
                   f'data-tip="{escape(f"{m}: {money(totals[i], currency)} ({parts})")}"/>')
        if i % step == 0:
            out.append(f'<text x="{gx:.1f}" y="{H - 8}" text-anchor="middle" class="axis">{escape(m)}</text>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{base}" y2="{base}" class="baseline"/></svg>')
    return "".join(out)


def stacked_area(points: list[tuple[str, list[float]]], names: list[str], currency: str, label: str) -> str:
    """Parts of a total over time. points: (iso date, value per name). Negative values (debts) stack below zero."""
    if len(points) < 2:
        return '<p class="muted">Needs at least two dates.</p>'
    W, H, L, R, T, B = 640, 280, 56, 12, 16, 30
    days = [date.fromisoformat(d).toordinal() for d, _ in points]
    x0, x1 = days[0], days[-1]
    maxpos = max(sum(max(v, 0) for v in vals) for _, vals in points) or 1.0
    maxneg = max(sum(-min(v, 0) for v in vals) for _, vals in points)
    top, ticks = nice_axis(maxpos)
    step = ticks[1] - ticks[0]
    floor = -math.ceil(maxneg / step) * step if maxneg else 0.0
    ticks = [floor + i * step for i in range(int(round((top - floor) / step)) + 1)]

    def sx(d):
        return L + (W - L - R) * (d - x0) / ((x1 - x0) or 1)

    def sy(v):
        return T + (H - T - B) * (1 - (v - floor) / (top - floor))

    out = [_swatches(names), f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{escape(label)}" class="chart">']
    for t in ticks:
        y = sy(t)
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end" class="axis">{escape(compact(t, currency))}</text>')
    pos = [0.0] * len(points)
    neg = [0.0] * len(points)
    for k, name in enumerate(names):
        lo, hi = [], []
        for i, (_, vals) in enumerate(points):
            v = vals[k]
            if v >= 0:
                lo.append(pos[i]); hi.append(pos[i] + v); pos[i] += v
            else:
                hi.append(neg[i]); lo.append(neg[i] + v); neg[i] += v
        upper = " ".join(f"{sx(d):.1f},{sy(h):.1f}" for d, h in zip(days, hi))
        lower = " ".join(f"{sx(d):.1f},{sy(l):.1f}" for d, l in reversed(list(zip(days, lo))))
        out.append(f'<polygon points="{upper} {lower}" fill="var({_color(k, name)})" stroke="var(--surface)" stroke-width="1"/>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(0):.1f}" y2="{sy(0):.1f}" class="baseline"/>')
    for i, (iso, vals) in enumerate(points):
        cx = sx(days[i])
        w = max(((sx(days[i + 1]) - cx) if i + 1 < len(days) else (cx - sx(days[i - 1]))) , 6)
        parts = ", ".join(f"{nme} {money(v, currency)}" for nme, v in zip(names, vals) if v)
        out.append(f'<rect x="{cx - w / 2:.1f}" y="{T}" width="{w:.1f}" height="{H - T - B}" fill="transparent" '
                   f'data-tip="{escape(f"{iso}: {money(sum(vals), currency)} ({parts})")}"/>')
    xs = [sx(d) for d in days]
    keep: list[int] = []
    for i, cx in enumerate(xs):
        if not keep or cx - xs[keep[-1]] >= 70:
            keep.append(i)
    if keep[-1] != len(xs) - 1:
        if xs[-1] - xs[keep[-1]] >= 70:
            keep.append(len(xs) - 1)
        else:
            keep[-1] = len(xs) - 1
    for i in keep:
        anchor = "end" if xs[i] > W - R - 30 else ("start" if xs[i] < L + 30 else "middle")
        out.append(f'<text x="{xs[i]:.1f}" y="{H - 8}" text-anchor="{anchor}" class="axis">{escape(points[i][0][:7])}</text>')
    out.append("</svg>")
    return "".join(out)


def pace_chart(this: list[float | None], usual: list[float], low: list[float], high: list[float], currency: str, label: str, month: str) -> str:
    """Spending added up day by day (solid), against the usual month (dashed) and the range of earlier months (band)."""
    n = len(usual)
    if n < 2:
        return ""
    W, H, L, R, T, B = 640, 240, 56, 56, 16, 30
    top, ticks = nice_axis(max([*high, *[v for v in this if v is not None]]) or 1)

    def sx(day):                       # day 1..n
        return L + (W - L - R) * (day - 1) / (n - 1)

    def sy(v):
        return T + (H - T - B) * (1 - v / top)

    out = [_legend([("This month, expenses so far", "--s2"), ("Usual month", "--axis")]),
           f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{escape(label)}" class="chart">']
    for t in ticks:
        y = sy(t)
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end" class="axis">{escape(compact(t, currency))}</text>')
    up = " ".join(f"{sx(d + 1):.1f},{sy(v):.1f}" for d, v in enumerate(high))
    dn = " ".join(f"{sx(d + 1):.1f},{sy(v):.1f}" for d, v in reversed(list(enumerate(low))))
    out.append(f'<polygon points="{up} {dn}" fill="var(--grid)" opacity=".7"/>')
    out.append('<polyline points="' + " ".join(f"{sx(d + 1):.1f},{sy(v):.1f}" for d, v in enumerate(usual))
               + '" fill="none" stroke="var(--muted)" stroke-width="1.5" stroke-dasharray="5 4"/>')
    pts = [(d + 1, v) for d, v in enumerate(this) if v is not None]
    if pts:
        out.append('<polyline points="' + " ".join(f"{sx(d):.1f},{sy(v):.1f}" for d, v in pts)
                   + '" fill="none" stroke="var(--s2)" stroke-width="2.5" stroke-linejoin="round"/>')
        d, v = pts[-1]
        out.append(f'<circle cx="{sx(d):.1f}" cy="{sy(v):.1f}" r="4.5" fill="var(--s2)" stroke="var(--surface)" stroke-width="2"/>'
                   f'<text x="{sx(d) + 8:.1f}" y="{sy(v) + 4:.1f}" class="direct">{escape(compact(v, currency))}</text>')
    for d in range(n):
        v = this[d] if d < len(this) else None
        tip = f"{d + 1}/{month[5:7]}: " + (f"spent {money(v, currency)} so far, " if v is not None else "") + f"usually {money(usual[d], currency)} by now"
        out.append(f'<rect x="{sx(d + 1) - (W - L - R) / (n - 1) / 2:.1f}" y="{T}" width="{(W - L - R) / (n - 1):.1f}" height="{H - T - B}" '
                   f'fill="transparent" data-tip="{escape(tip)}"/>')
    for d in range(1, n + 1, 5):
        out.append(f'<text x="{sx(d):.1f}" y="{H - 8}" text-anchor="middle" class="axis">{d}</text>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(0):.1f}" y2="{sy(0):.1f}" class="baseline"/></svg>')
    return "".join(out)
