"""Optional starter setup from a file next to the app (starter_rules.json), applied with one click.

Kept as a file, not code, so personal details (employer names, account names) never live in the program itself.
Schema: {"owners": [{"name", "aliases": []}], "accounts": [{"label", "kind", "owner", "issuer"}],
         "rules": [{"pattern", "category", "kind", "direction"}],
         "destination_rules": [{"pattern", "account"}],
         "sheet": {"doc_url": "https://docs.google.com/spreadsheets/d/…/edit", "token": "…"}}
Applying it twice changes nothing.
"""
import json
import sqlite3
from pathlib import Path

from . import accounts, categorize
from .normalize import norm_description

FILENAME = "starter_rules.json"


def find(app_dir: Path) -> Path | None:
    p = app_dir / FILENAME
    return p if p.exists() else None


def apply(conn: sqlite3.Connection, path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    done = {"owners": 0, "accounts": 0, "rules": 0, "destinations": 0}
    for o in data.get("owners", []):
        accounts.add_owner(conn, o["name"], o.get("aliases", []))
        done["owners"] += 1
    for a in data.get("accounts", []):
        if not conn.execute("SELECT 1 FROM accounts WHERE label = ?", (a["label"],)).fetchone():
            accounts.add_account(conn, a.get("kind", "savings"), a.get("issuer", "manual"), a["label"], a.get("owner"),
                                 a.get("currency", "ILS"))
            done["accounts"] += 1
    for r in data.get("rules", []):
        if not conn.execute("SELECT 1 FROM rules WHERE source = 'user' AND pattern = ?", (norm_description(r["pattern"]),)).fetchone():
            categorize.add_user_rule(conn, r["pattern"], r["category"], r.get("kind"), r.get("direction"))
            done["rules"] += 1
    for d in data.get("destination_rules", []):
        accounts.set_destination_rule(conn, d["pattern"], d["account"])
        done["destinations"] += 1
    sheet = data.get("sheet") or {}
    if sheet.get("token") and not conn.execute("SELECT 1 FROM settings WHERE key = 'sheet_token'").fetchone():
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sheet_token', ?)", (sheet["token"],))
    if sheet.get("doc_url"):
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sheet_doc_url', ?)", (sheet["doc_url"],))
    conn.commit()
    return done
