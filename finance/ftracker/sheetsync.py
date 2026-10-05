"""Keep the shared Google Sheet up to date automatically.

How it is linked (once): a small Apps Script, pasted into your Sheet, receives the submitted
numbers from this app. No Google Cloud project or API keys are involved; the script only accepts
requests carrying a secret this app generates. After that, every Submit pushes to the Sheet.
"""
import json
import secrets
import sqlite3
import urllib.request
from datetime import datetime
from importlib import resources
from urllib.parse import urlparse

from . import export

URL_PREFIX = "https://script.google.com/macros/s/"


def _get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def _set_setting(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def token(conn: sqlite3.Connection, reset: bool = False) -> str:
    t = _get_setting(conn, "sheet_token")
    if reset or not t:
        t = secrets.token_hex(16)
        _set_setting(conn, "sheet_token", t)
        conn.commit()
    return t


def script_text(conn: sqlite3.Connection, reset: bool = False) -> str:
    template = resources.files("ftracker").joinpath("sheet_sync", "Code.gs").read_text(encoding="utf-8")
    return template.replace("__TOKEN__", token(conn, reset))


def valid_url(url: str) -> bool:
    u = urlparse(url.strip())
    return url.strip().startswith(URL_PREFIX) and u.path.endswith("/exec") and not u.query


def link(conn: sqlite3.Connection, url: str) -> None:
    if not valid_url(url):
        raise ValueError("That is not a web app address. It should look like "
                         "https://script.google.com/macros/s/…/exec (copy it from Deploy > New deployment).")
    _set_setting(conn, "sheet_url", url.strip())
    conn.commit()


def unlink(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM settings WHERE key IN ('sheet_url', 'sheet_last_sync', 'sheet_last_result')")
    conn.commit()


def set_notes(conn: sqlite3.Connection, on: bool) -> None:
    _set_setting(conn, "sheet_notes", "1" if on else "0")
    conn.commit()


def status(conn: sqlite3.Connection) -> dict:
    url = _get_setting(conn, "sheet_url")
    return {"linked": bool(url), "last_sync": _get_setting(conn, "sheet_last_sync"),
            "last_result": _get_setting(conn, "sheet_last_result"), "notes": _get_setting(conn, "sheet_notes", "0") == "1",
            "has_token": bool(_get_setting(conn, "sheet_token"))}


def build_payload(conn: sqlite3.Connection, today: str) -> dict:
    tables = export.collect_tables(conn, today, _get_setting(conn, "sheet_notes", "0") == "1")
    return {"token": token(conn), "sheets": [{"name": n, "header": h, "rows": r} for n, h, r in tables]}


def push(conn: sqlite3.Connection, today: str, opener=None) -> dict:
    """Send the current submitted numbers. Never raises; returns {'ok': bool, 'detail': str}."""
    url = _get_setting(conn, "sheet_url")
    if not url:
        return {"ok": False, "detail": "The Google Sheet is not linked yet."}
    payload = build_payload(conn, today)
    req = urllib.request.Request(url, data=json.dumps(payload, default=str).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with (opener or urllib.request.urlopen)(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        return _record(conn, False, f"Could not reach Google ({exc}). It will retry on the next submit.")
    try:
        answer = json.loads(raw)
    except ValueError:
        return _record(conn, False, "Google answered with a web page, not the link script. Redeploy it as a web app "
                                    "with “Who has access: Anyone”, then paste the new address.")
    if answer.get("ok"):
        return _record(conn, True, f"{answer.get('sheets', len(payload['sheets']))} sheet tab(s) updated")
    if answer.get("error") == "wrong token":
        return _record(conn, False, "The script in your Sheet has a different secret. Paste the current script again.")
    return _record(conn, False, f"The Sheet script reported: {answer.get('error', 'unknown error')}")


def _record(conn, ok: bool, detail: str) -> dict:
    _set_setting(conn, "sheet_last_result", ("ok: " if ok else "failed: ") + detail)
    if ok:
        _set_setting(conn, "sheet_last_sync", datetime.now().isoformat(timespec="seconds"))
    conn.commit()
    return {"ok": ok, "detail": detail}


def sync_if_linked(conn: sqlite3.Connection, today: str, opener=None) -> str | None:
    """Called after every Submit. A failure is a warning, never a reason to undo the submit."""
    if not _get_setting(conn, "sheet_url"):
        return None
    res = push(conn, today, opener)
    return ("Google Sheet updated" if res["ok"] else "Google Sheet NOT updated: " + res["detail"])
