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

    out = [_legend([("Income", "--s1"), ("Spending", "--s2")]),
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
