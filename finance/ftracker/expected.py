"""The checklist of files you should have uploaded, worked out from what the app already knows."""
import json
import sqlite3
from collections import Counter
from datetime import date, timedelta

from . import balances

BANK_STALE_DAYS = 35


def _months_between(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def expected_files(conn: sqlite3.Connection, today: str) -> list[dict]:
    now_d = date.fromisoformat(today)
    items: list[dict] = []
    row = conn.execute("SELECT value FROM settings WHERE key = 'history_months'").fetchone()
    history = set(json.loads(row[0])) if row else set()

    for card in conn.execute("SELECT * FROM accounts WHERE kind = 'card' AND active = 1"):
        stmts = [date.fromisoformat(r[0]) for r in conn.execute(
            "SELECT billing_date FROM statements WHERE account_id = ? AND billing_date IS NOT NULL", (card["id"],))]
        if not stmts:
            continue
        day = Counter(d.day for d in stmts).most_common(1)[0][0]
        covered = {r[0] for r in conn.execute("SELECT month FROM coverage WHERE account_id = ?", (card["id"],))}
        have = {(d.year, d.month) for d in stmts}
        for y, m in _months_between(min(stmts), now_d):
            if (y, m) in have or f"{y:04d}-{m:02d}" in covered or f"{y:04d}-{m:02d}" in history:
                continue
            due = date(y, m, min(day, 28))
            if due >= now_d:
                continue
            late = (now_d - due).days > 3
            items.append({"source": card["label"], "period": f"{y:04d}-{m:02d}",
                          "status": "missing" if late else "due",
                          "detail": f"statement billed around {due.isoformat()}"})

    for bank in conn.execute("SELECT * FROM accounts WHERE kind = 'bank' AND active = 1"):
        spans = sorted((r["period_start"], r["period_end"]) for r in conn.execute(
            "SELECT period_start, period_end FROM statements WHERE account_id = ? AND period_start IS NOT NULL", (bank["id"],)))
        if not spans:
            continue
        for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
            if next_start > prev_end and (date.fromisoformat(next_start) - date.fromisoformat(prev_end)).days > 1:
                items.append({"source": bank["label"], "period": f"{prev_end} to {next_start}",
                              "status": "missing", "detail": "gap between two bank statements"})
        last_end = date.fromisoformat(max(e for _, e in spans))
        if (now_d - last_end).days > BANK_STALE_DAYS:
            items.append({"source": bank["label"], "period": f"after {last_end.isoformat()}",
                          "status": "due", "detail": f"latest statement ends {last_end.isoformat()}"})

    due, last = balances.balances_due(conn, today)
    if due:
        items.append({"source": "Balances", "period": "now", "status": "due",
                      "detail": f"last updated {last}" if last else "never entered"})
    for r in balances.stale(conn, today):
        items.append({"source": r["label"], "period": r["as_of"], "status": "stale",
                      "detail": f"balance last updated {r['as_of']}"})

    # Anything the bank paid that has no statement is raised as a discrepancy; list it here too.
    for r in conn.execute("SELECT title FROM discrepancies WHERE type = 'missing_statement' AND status = 'open'"):
        items.append({"source": "Card statement", "period": "", "status": "missing", "detail": r["title"]})
    return items


# Sources you should have even before the first file arrives. `optional` ones never block anything.
EXTRA_SOURCES = [
    {"name": "Shir's Isracard", "kind": "card", "issuer": "isracard", "owner": "Shir", "optional": False},
    {"name": "Member card (Max It)", "kind": "card", "label_contains": "בהצדעה", "optional": True},
    {"name": "Cal 5962 (new card)", "kind": "card", "last4": "5962", "optional": True,
     "detail": "make it required once you use it; upload its first export so the app can learn to read it"},
    {"name": "Shir's Leumi account", "kind": "bank", "issuer": "leumi", "owner": "Shir", "optional": True},
]


def _month_label(y: int, m: int) -> str:
    return date(y, m, 1).strftime("%B %Y")


def _has_statement(conn, account_id: int, y: int, m: int, covered: set[str]) -> str | None:
    key = f"{y:04d}-{m:02d}"
    row = conn.execute("SELECT billing_date FROM statements WHERE account_id = ? AND billing_date LIKE ? LIMIT 1",
                       (account_id, key + "%")).fetchone()
    if row:
        return f"billed {row[0]}"
    return "already in your history" if key in covered else None


def _is_optional(conn, a) -> bool:
    """True when this account matches one of the optional sources (for example Shir's Leumi account)."""
    owner = conn.execute("SELECT name FROM owners WHERE id = ?", (a["owner_id"],)).fetchone()
    owner = owner[0] if owner else None
    for src in EXTRA_SOURCES:
        if not src["optional"] or src["kind"] != a["kind"]:
            continue
        if src.get("issuer") and a["issuer"] != src["issuer"]:
            continue
        if src.get("owner") and owner != src["owner"]:
            continue
        if src.get("label_contains") and src["label_contains"] not in a["label"]:
            continue
        if src.get("last4") and a["last4"] != src["last4"]:
            continue
        return True
    return False


def checklist(conn: sqlite3.Connection, today: str) -> dict:
    """Everything that should be uploaded or entered for the current round, each with a done/to-do state.

    Uploading a file changes the state because the state is worked out from what is stored.
    """
    now_d = date.fromisoformat(today)
    row = conn.execute("SELECT value FROM settings WHERE key = 'history_months'").fetchone()
    history = set(json.loads(row[0])) if row else set()
    prev = (now_d.year, now_d.month - 1) if now_d.month > 1 else (now_d.year - 1, 12)
    cards, banks, other = [], [], []
    matched_accounts: set[int] = set()

    def owner_name(a):
        r = conn.execute("SELECT name FROM owners WHERE id = ?", (a["owner_id"],)).fetchone()
        return r[0] if r else None

    for a in conn.execute("SELECT * FROM accounts WHERE kind IN ('card','bank') AND active = 1 AND issuer != 'sheet' ORDER BY kind, label"):
        matched_accounts.add(a["id"])
        if a["kind"] == "card":
            stmts = [date.fromisoformat(r[0]) for r in conn.execute(
                "SELECT billing_date FROM statements WHERE account_id = ? AND billing_date IS NOT NULL", (a["id"],))]
            if not stmts:
                continue
            day = Counter(d.day for d in stmts).most_common(1)[0][0]
            covered = {r[0] for r in conn.execute("SELECT month FROM coverage WHERE account_id = ?", (a["id"],))} | history
            first = min(stmts)
            for y, m in (prev, (now_d.year, now_d.month)):
                if (y, m) < (first.year, first.month):
                    continue
                got = _has_statement(conn, a["id"], y, m, covered)
                due = date(y, m, min(day, 28))
                if got:
                    cards.append({"label": f"{a['label']} · {_month_label(y, m)}", "status": "done", "detail": got})
                elif due > now_d:
                    cards.append({"label": f"{a['label']} · {_month_label(y, m)}", "status": "waiting",
                                  "detail": f"not due yet, billed around {due.isoformat()}"})
                else:
                    cards.append({"label": f"{a['label']} · {_month_label(y, m)}", "status": "optional" if _is_optional(conn, a) else "todo",
                                  "detail": f"billed around {due.isoformat()}"})
        else:
            ends = [r[0] for r in conn.execute(
                "SELECT period_end FROM statements WHERE account_id = ? AND period_end IS NOT NULL", (a["id"],))]
            if not ends:
                continue
            last = date.fromisoformat(max(ends))
            fresh = (now_d - last).days <= BANK_STALE_DAYS
            banks.append({"label": a["label"], "status": "done" if fresh else ("optional" if _is_optional(conn, a) else "todo"),
                          "detail": f"covers up to {last.isoformat()}" if fresh else f"latest statement ends {last.isoformat()}"})

    for src in EXTRA_SOURCES:
        found = False
        for a in conn.execute("SELECT * FROM accounts WHERE kind = ? AND active = 1 AND issuer != 'sheet'", (src["kind"],)):
            if src.get("issuer") and a["issuer"] != src["issuer"]:
                continue
            if src.get("owner") and owner_name(a) != src["owner"]:
                continue
            if src.get("label_contains") and src["label_contains"] not in a["label"]:
                continue
            if src.get("last4") and a["last4"] != src["last4"]:
                continue
            found = conn.execute("SELECT 1 FROM statements WHERE account_id = ?", (a["id"],)).fetchone() is not None
            if found:
                break
        if found:
            continue            # already listed above from its own account
        target = banks if src["kind"] == "bank" else cards
        target.append({"label": src["name"], "status": "optional" if src["optional"] else "todo",
                       "detail": src.get("detail") or ("nice to have, not required" if src["optional"] else "not uploaded yet")})

    pend = balances.pending(conn, today)
    todo = [p for p in pend if not p["current"]]
    kids = []
    for p in pend:
        l, a = p["latest"], p["account"]
        who = f"{a['owner']} · " if a["owner"] else ""
        kids.append({"label": a["label"],
                     "status": "done" if p["current"] else "todo",
                     "detail": who + ((f"updated {l['as_of']}" if p["current"] else f"last value {l['as_of']}") if l else "never entered")})
    other.append({"label": "Balances (monthly)", "status": "todo" if todo else "done",
                  "detail": (f"{len(todo)} of {len(pend)} accounts need a number" if todo else f"all {len(pend)} accounts are up to date"),
                  "link": "/balances", "children": kids})
    groups = [{"title": "Card statements", "items": cards}, {"title": "Bank statements", "items": banks},
              {"title": "Numbers you type", "items": other}]
    counted = [i for g in groups for i in g["items"] if i["status"] in ("done", "todo")]
    return {"groups": groups, "done": sum(1 for i in counted if i["status"] == "done"), "total": len(counted)}


def _covered(spans: list[tuple[date, date]], start: date, end: date) -> bool:
    """True when the statement periods together cover every day from start to end."""
    day = start
    for s, e in sorted(spans):
        if s > day:
            return False
        day = max(day, e + timedelta(days=1))
        if day > end:
            return True
    return day > end


def history(conn: sqlite3.Connection, today: str, max_months: int = 24) -> list[dict]:
    """The checklist for the months before the last two, newest first, so you can see what is complete and what has a gap."""
    now_d = date.fromisoformat(today)
    row = conn.execute("SELECT value FROM settings WHERE key = 'history_months'").fetchone()
    hist = set(json.loads(row[0])) if row else set()
    newest = (now_d.year, now_d.month - 2)
    while newest[1] < 1:
        newest = (newest[0] - 1, newest[1] + 12)
    accounts = conn.execute("SELECT * FROM accounts WHERE kind IN ('card','bank') AND active = 1 AND issuer != 'sheet' ORDER BY kind, label").fetchall()
    info = []
    first_month = None
    for a in accounts:
        if a["kind"] == "card":
            have = {(date.fromisoformat(r[0]).year, date.fromisoformat(r[0]).month) for r in conn.execute(
                "SELECT billing_date FROM statements WHERE account_id = ? AND billing_date IS NOT NULL", (a["id"],))}
            if not have:
                continue
            info.append((a, have, None))
            first_month = min(first_month or min(have), min(have))
        else:
            spans = [(date.fromisoformat(r[0]), date.fromisoformat(r[1])) for r in conn.execute(
                "SELECT period_start, period_end FROM statements WHERE account_id = ? AND period_start IS NOT NULL", (a["id"],))]
            if not spans:
                continue
            info.append((a, None, spans))
            fm = (min(s for s, _ in spans).year, min(s for s, _ in spans).month)
            first_month = min(first_month or fm, fm)
    if not first_month:
        return []
    months, (y, m) = [], newest
    while (y, m) >= first_month and len(months) < max_months:
        months.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    out = []
    for y, m in months:
        key = f"{y:04d}-{m:02d}"
        start = date(y, m, 1)
        end = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        items = []
        for a, have, spans in info:
            optional = _is_optional(conn, a)
            if have is not None:
                firsts = min(have)
                if (y, m) < firsts:
                    continue
                if (y, m) in have:
                    items.append({"label": a["label"], "status": "done", "detail": "received"})
                elif key in hist:
                    items.append({"label": a["label"], "status": "done", "detail": "already in your history"})
                else:
                    items.append({"label": a["label"], "status": "optional" if optional else "todo", "detail": "no statement for this month"})
            else:
                first = min(s for s, _ in spans)
                if end < first:
                    continue
                if key in hist and start < first:
                    items.append({"label": a["label"], "status": "done", "detail": "already in your history"})
                elif _covered(spans, start + timedelta(days=4), end - timedelta(days=4)):
                    items.append({"label": a["label"], "status": "done", "detail": "covered by a statement"})
                else:
                    items.append({"label": a["label"], "status": "optional" if optional else "todo", "detail": "gap in the bank statements"})
        if items:
            counted = [i for i in items if i["status"] in ("done", "todo")]
            out.append({"key": key, "label": _month_label(y, m), "items": items,
                        "done": sum(1 for i in counted if i["status"] == "done"), "total": len(counted)})
    return out
