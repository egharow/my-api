"""One-time corrections to your own data, listed in a data_fixes.json file next to the program.

Each entry has an id and is applied once (the id is remembered), after a backup of your database is made.
Actions: rename_account, close_account, delete_account, merge_accounts, set_balances.
"""
import json
import sqlite3
import traceback
from datetime import date
from pathlib import Path

from . import accounts
from .backup import make_backup
from .config import Home
from .db import audit

FILE = "data_fixes.json"


def _entries(app_dir: Path) -> list[dict]:
    path = app_dir / FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return [e for e in data if isinstance(e, dict) and e.get("id") and e.get("action")]


def _done(conn: sqlite3.Connection, fix_id: str) -> bool:
    return conn.execute("SELECT 1 FROM settings WHERE key = ?", (f"datafix:{fix_id}",)).fetchone() is not None


def pending(conn: sqlite3.Connection, app_dir: Path) -> list[dict]:
    return [e for e in _entries(app_dir) if not _done(conn, e["id"])]


def _account(conn, label: str):
    return conn.execute("SELECT id FROM accounts WHERE label = ? ORDER BY active DESC, id LIMIT 1", (label,)).fetchone()


def _run(conn: sqlite3.Connection, e: dict, today: date) -> str:
    action = e["action"]
    acct = _account(conn, e.get("label") or e.get("from") or "")
    if action == "merge_accounts":
        dst = _account(conn, e["into"])
        if acct and dst:
            accounts.merge_accounts(conn, acct[0], dst[0])
            return f"{e['from']} merged into {e['into']}"
        return f"skipped: {e['from']} or {e['into']} not found"
    if not acct:
        return f"skipped: no account called {e.get('label')}"
    if action == "rename_account":
        accounts.rename_account(conn, acct[0], e["to"])
        return f"{e['label']} renamed to {e['to']}"
    if action == "close_account":
        on = today.isoformat() if e.get("on") in (None, "today") else e["on"]
        accounts.close_account(conn, acct[0], on)
        return f"{e['label']} closed on {on} (history kept)"
    if action == "delete_account":
        accounts.delete_account(conn, acct[0])
        return f"{e['label']} deleted"
    if action == "set_balances":
        n = accounts.set_all_balances(conn, acct[0], float(e["amount"]), e["currency"])
        return f"{e['label']}: every balance set to {float(e['amount']):,.0f} {e['currency'].upper()} ({n} dates)"
    return f"skipped: unknown action {action}"


def apply(conn: sqlite3.Connection, home: Home, app_dir: Path, today: date | None = None) -> list[str]:
    todo = pending(conn, app_dir)
    if not todo:
        return []
    today = today or date.today()
    try:
        make_backup(conn, home, "before-data-fixes")
    except Exception:
        print(traceback.format_exc())
    lines = []
    for e in todo:
        try:
            lines.append(_run(conn, e, today))
        except Exception as exc:
            print(traceback.format_exc())
            lines.append(f"{e['id']} failed: {exc}")
            continue
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"datafix:{e['id']}", date.today().isoformat()))
        audit(conn, "data_fix", "settings", None, lines[-1])
        conn.commit()
    # show what changed on the Home page, in the same notice as the first-start summary
    row = conn.execute("SELECT value FROM settings WHERE key = 'seed_summary'").fetchone()
    notice = json.loads(row[0]) if row else []
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('seed_summary', ?)",
                 (json.dumps(notice + ["Corrections applied: " + "; ".join(lines)], ensure_ascii=False),))
    conn.commit()
    return lines
