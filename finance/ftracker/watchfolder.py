"""Pick up statements from a folder you choose (for example your Google Drive folder synced to this computer).

Files are copied into the app's inbox and imported like any dropped file. The original stays where it is.
A statement already imported is recognised by its content and skipped, so nothing is added twice.
"""
import json
import shutil
import sqlite3
import time
from datetime import date
from pathlib import Path

from . import importer
from .config import Home

KEY = "watch_folder"
SEEN_KEY = "watch_seen"
RESULT_KEY = "watch_last_result"
SUPPORTED = {".xlsx", ".xls", ".pdf"}
SETTLE_SECONDS = 8            # a file still being synced from Drive is left alone for a moment
_last_scan = {"t": 0.0}


def get(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()
    return row[0] if row and row[0] else None


def set_folder(conn: sqlite3.Connection, home: Home, raw: str) -> str:
    path = Path(raw.strip().strip('"')).expanduser()
    if not path.is_dir():
        raise ValueError("That folder was not found. Paste the full path, for example G:\\My Drive\\Statements")
    resolved = path.resolve()
    if home.root.resolve() in (resolved, *resolved.parents):
        raise ValueError("Choose a folder outside the app's own data folder.")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (KEY, str(resolved)))
    conn.commit()
    return str(resolved)


def clear(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM settings WHERE key IN (?, ?)", (KEY, SEEN_KEY))
    conn.commit()


def last_result(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (RESULT_KEY,)).fetchone()
    return row[0] if row else ""


def _seen(conn) -> set[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (SEEN_KEY,)).fetchone()
    return set(json.loads(row[0])) if row else set()


def scan(conn: sqlite3.Connection, home: Home, today: date | None = None, force: bool = False) -> str:
    """Import new statements from the watched folder. Returns a short message ('' when nothing happened)."""
    folder = get(conn)
    if not folder:
        return ""
    if not force and time.time() - _last_scan["t"] < 20:
        return ""
    _last_scan["t"] = time.time()
    root = Path(folder)
    if not root.is_dir():
        msg = f"The watched folder {folder} is not available right now (is Google Drive running?)."
        _save(conn, msg)
        return msg
    seen = _seen(conn)
    fresh: list[Path] = []
    for p in sorted(root.iterdir()):
        if not p.is_file() or p.suffix.lower() not in SUPPORTED or p.name.startswith(("~$", ".")):
            continue
        if time.time() - p.stat().st_mtime < SETTLE_SECONDS:
            continue
        digest = importer.sha256(p)
        if digest in seen or conn.execute("SELECT 1 FROM source_files WHERE sha256 = ?", (digest,)).fetchone():
            continue
        fresh.append(p)
        seen.add(digest)
    if not fresh:
        return ""
    home.inbox.mkdir(parents=True, exist_ok=True)
    for p in fresh:
        dest = home.inbox / p.name
        n = 1
        while dest.exists():
            dest = home.inbox / f"{p.stem}-{n}{p.suffix}"
            n += 1
        shutil.copy2(p, dest)
    report = importer.import_inbox(home, conn, today or date.today())
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (SEEN_KEY, json.dumps(sorted(seen))))
    imported = report.imported
    bad = [f for f in report.files if f.status == "unrecognised"]
    msg = f"{len(imported)} new statement(s) imported from your folder"
    if bad:
        msg += f"; {len(bad)} not recognised ({', '.join(f.name for f in bad[:3])})"
    _save(conn, msg)
    return msg


def _save(conn, msg: str) -> None:
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (RESULT_KEY, f"{msg} · {time.strftime('%Y-%m-%d %H:%M')}"))
    conn.commit()
