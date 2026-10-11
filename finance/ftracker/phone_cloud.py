"""A phone page that works when this computer is off.

The app sends a compact summary (months, categories, net worth) to your linked Google Sheet's script, which serves a
page for your phone behind a PIN. Nothing here needs the computer to be on: the last numbers sent stay in your Google account.
"""
import hashlib
import json
import secrets
import sqlite3
from datetime import datetime

from . import balances, sheetsync


def pin(conn: sqlite3.Connection) -> str | None:
    return sheetsync._get_setting(conn, "cloud_pin")


def turn_on(conn: sqlite3.Connection) -> str:
    p = f"{secrets.randbelow(10 ** 8):08d}"
    sheetsync._set_setting(conn, "cloud_pin", p)
    sheetsync._set_setting(conn, "cloud_hash", "")
    conn.commit()
    return p


def turn_off(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM settings WHERE key IN ('cloud_pin', 'cloud_hash')")
    conn.commit()


def pin_hash(conn: sqlite3.Connection, p: str) -> str:
    return hashlib.sha256(f"{sheetsync.token(conn)}:{p}".encode()).hexdigest()


def build_snapshot(conn: sqlite3.Connection, today: str) -> dict:
    from .views_overview import _load
    d = _load(conn, None)
    months = []
    for m in d["months"]:
        months.append({"m": m, "inc": round(d["inc_tot"].get(m, 0)), "exp": round(d["exp_tot"].get(m, 0)), "inv": round(d["inv"].get(m, 0)),
                       "cats": {c: round(v) for c, v in d["spend"].get(m, {}).items()},
                       "src": {k: round(v) for k, v in d["inc"].get(m, {}).items()}})
    series, lines = [], []
    try:
        dates = [r[0] for r in conn.execute("SELECT DISTINCT as_of FROM balances ORDER BY as_of")]
        for dt in dates:
            nw = balances.net_worth(conn, dt, "ILS")
            series.append({"d": dt, "total": round(nw["total"])})
        if dates:
            lines = [{"label": l["account"], "kind": l["kind"], "value": round(l["value"])} for l in balances.net_worth(conn, dates[-1], "ILS")["lines"]]
    except LookupError:
        series, lines = [], []
    return {"generated": datetime.now().isoformat(timespec="minutes"), "today": today, "months": months,
            "networth": {"series": series, "lines": lines}}


def payload_extras(conn: sqlite3.Connection, today: str) -> dict:
    p = pin(conn)
    if not p:
        return {}
    return {"snapshot": build_snapshot(conn, today), "pin_hash": pin_hash(conn, p)}


def _digest(extras: dict) -> str:
    snap = dict(extras.get("snapshot", {}))
    snap.pop("generated", None)
    return hashlib.sha256(json.dumps(snap, sort_keys=True, default=str).encode()).hexdigest()


def maybe_push(conn: sqlite3.Connection, today: str, opener=None) -> bool:
    """Send the summary if it changed since the last time. Quiet: a failure never interrupts anything."""
    if not pin(conn) or not sheetsync._get_setting(conn, "sheet_url"):
        return False
    extras = payload_extras(conn, today)
    digest = _digest(extras)
    if digest == sheetsync._get_setting(conn, "cloud_hash"):
        return False
    res = sheetsync.push(conn, today, opener)
    if res["ok"]:
        sheetsync._set_setting(conn, "cloud_hash", digest)
        conn.commit()
    return res["ok"]
