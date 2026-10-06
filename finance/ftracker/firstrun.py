"""First launch: load everything you already gave me, so you do not do it twice.

Looks next to the app for:
  starter_rules.json   people, name matching, accounts, income rules (applied by starter.py)
  seed/history.xlsx    your old budget sheet
  seed/statements/*    statements you already sent
and loads each once. It is safe on a data folder that already has some of it: whatever is already there is
recognised and skipped. Everything arrives as a draft for you to review; nothing is submitted for you.
"""
import json
import shutil
import sqlite3
import traceback
from datetime import date, datetime
from pathlib import Path

from . import categorize, importer, sheet_import, starter
from .config import Home

KEY = "seed_applied"
SUMMARY_KEY = "seed_summary"


def seed_dir(app_dir: Path) -> Path:
    return app_dir / "seed"


def has_seed(app_dir: Path) -> bool:
    return starter.find(app_dir) is not None or seed_dir(app_dir).exists()


def needed(conn: sqlite3.Connection, app_dir: Path) -> bool:
    done = conn.execute("SELECT 1 FROM settings WHERE key = ?", (KEY,)).fetchone()
    return not done and has_seed(app_dir)


def _set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def run(conn: sqlite3.Connection, home: Home, app_dir: Path, today: date | None = None) -> dict:
    today = today or date.today()
    summary: dict = {"steps": []}
    sd = seed_dir(app_dir)

    found = starter.find(app_dir)
    if found:
        done = starter.apply(conn, found)
        summary["steps"].append(f"{done['owners']} people, {done['accounts']} accounts and {done['rules']} income rules set up")

    history = sd / "history.xlsx"
    if history.exists():
        try:
            owners = [r[0] for r in conn.execute("SELECT name FROM owners ORDER BY id")]
            if owners:
                data = sheet_import.parse_workbook(history)
                res = sheet_import.save(conn, data, owners[0], owners[1] if len(owners) > 1 else owners[0])
                if res["batch_id"] is None:
                    summary["steps"].append("your old budget sheet was already imported")
                else:
                    summary["steps"].append(f"old budget sheet: {res['transactions']:,} lines, {res['balances']} balances, "
                                            f"{res['rules_learned']} rules learned from your categories")
            else:
                summary["steps"].append("old budget sheet skipped: no people are set up yet")
        except Exception:
            summary["steps"].append("old budget sheet could not be read (see app-log.txt)")
            print(traceback.format_exc())

    statements = sorted(p for p in (sd / "statements").glob("*") if p.is_file()) if (sd / "statements").exists() else []
    if statements:
        home.inbox.mkdir(parents=True, exist_ok=True)
        for p in statements:
            dest = home.inbox / p.name
            if not dest.exists():
                shutil.copy2(p, dest)
        try:
            report = importer.import_inbox(home, conn, today)
            imported = [f for f in report.files if f.status == "imported"]
            dup = [f for f in report.files if f.status == "duplicate"]
            bad = [f for f in report.files if f.status == "unrecognised"]
            line = f"{len(imported)} statement file(s) imported ({sum(f.new_txns for f in imported)} lines)"
            if dup:
                line += f", {len(dup)} already imported"
            if bad:
                line += f", {len(bad)} not recognised"
            summary["steps"].append(line)
        except Exception:
            summary["steps"].append("statements could not be imported (see app-log.txt)")
            print(traceback.format_exc())

    _set(conn, KEY, datetime.now().isoformat(timespec="seconds"))
    _set(conn, SUMMARY_KEY, json.dumps(summary["steps"], ensure_ascii=False))
    conn.commit()
    return summary


def pending_notice(conn: sqlite3.Connection) -> list[str] | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (SUMMARY_KEY,)).fetchone()
    return json.loads(row[0]) if row else None


def dismiss_notice(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM settings WHERE key = ?", (SUMMARY_KEY,))
    conn.commit()


REPAIR_KEY = "repair_old_sheet_categories_v5"


def merge_duplicate_leumi(conn: sqlite3.Connection) -> bool:
    """The old sheet's "לאומי" account and the Leumi statement account are one account: keep the statement one."""
    from . import accounts
    src = conn.execute("SELECT id FROM accounts WHERE kind = 'bank' AND issuer = 'manual' AND label = 'לאומי' AND active = 1").fetchone()
    dst = conn.execute("SELECT id FROM accounts WHERE kind = 'bank' AND issuer = 'leumi' AND active = 1 ORDER BY id LIMIT 1").fetchone()
    if not src or not dst:
        return False
    accounts.merge_accounts(conn, src[0], dst[0])
    return True


def repair_done(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM settings WHERE key = ?", (REPAIR_KEY,)).fetchone() is not None


def import_missing_statements(conn: sqlite3.Connection, home: Home, app_dir: Path, today: date | None = None) -> int:
    """Import any statement you gave me that is not in your data yet (for example one that failed on an earlier start)."""
    folder = seed_dir(app_dir) / "statements"
    if not folder.exists():
        return 0
    missing = [p for p in sorted(folder.glob("*")) if p.is_file()
               and not conn.execute("SELECT 1 FROM source_files WHERE sha256 = ?", (importer.sha256(p),)).fetchone()]
    if not missing:
        return 0
    home.inbox.mkdir(parents=True, exist_ok=True)
    for p in missing:
        shutil.copy2(p, home.inbox / p.name)
    try:
        report = importer.import_inbox(home, conn, today or date.today())
    except Exception:
        print(traceback.format_exc())
        return 0
    for f in report.files:
        print(f"statement {f.name}: {f.status}" + (f" ({getattr(f, 'detail', '')})" if getattr(f, 'detail', '') else ""))
    return sum(1 for f in report.files if f.status == "imported")


def repair(conn: sqlite3.Connection, app_dir: Path, home: Home | None = None) -> int:
    """One-time: restore categories you set in the old sheet that an earlier build overrode (Bit lines, Investment)."""
    if conn.execute("SELECT 1 FROM settings WHERE key = ?", (REPAIR_KEY,)).fetchone():
        return 0
    history = seed_dir(app_dir) / "history.xlsx"
    fixed = 0
    if history.exists():
        fixed = sheet_import.restore_categories(conn, sheet_import.parse_workbook(history))
    categorize.resuggest(conn)
    if home is not None:
        import_missing_statements(conn, home, app_dir)
    merge_duplicate_leumi(conn)
    _set(conn, REPAIR_KEY, str(fixed))
    conn.commit()
    return fixed
