"""Backups to a folder you choose (your Google Drive folder synced to this computer).

Two things go there, in a FinanceBackup folder: a copy of the database, and a copy of every statement
you drop in (kept in the same folders as the app's own archive). The live database is never placed
inside the synced folder, only snapshots of it, so syncing cannot damage it.
"""
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from .backup import make_backup
from .config import Home

KEY = "backup_folder"
RESULT_KEY = "backup_last_result"
OK_KEY = "backup_last_ok"
SUBFOLDER = "FinanceBackup"
KEEP_DB = 40
EVERY_SECONDS = 6 * 3600


def get(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()
    return row[0] if row and row[0] else None


def set_folder(conn: sqlite3.Connection, home: Home, raw: str) -> str:
    path = Path(raw.strip().strip('"')).expanduser()
    if not path.is_dir():
        raise ValueError("That folder was not found. Paste the full path, for example G:\\My Drive")
    resolved = path.resolve()
    if home.root.resolve() in (resolved, *resolved.parents):
        raise ValueError("Choose a folder outside the app's own data folder.")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (KEY, str(resolved)))
    conn.commit()
    return str(resolved)


def clear(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM settings WHERE key IN (?, ?, ?)", (KEY, RESULT_KEY, OK_KEY))
    conn.commit()


def last_result(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (RESULT_KEY,)).fetchone()
    return row[0] if row else ""


def _last_ok(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (OK_KEY,)).fetchone()
    try:
        return float(row[0]) if row else 0.0
    except ValueError:
        return 0.0


def _save(conn: sqlite3.Connection, msg: str, ok: bool) -> None:
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (RESULT_KEY, f"{msg} · {time.strftime('%d/%m/%Y %H:%M')}"))
    if ok:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (OK_KEY, str(time.time())))
    conn.commit()


def run(conn: sqlite3.Connection, home: Home, reason: str = "backup") -> str:
    """Copy the database and any statements not yet copied. Returns a short message."""
    folder = get(conn)
    if not folder:
        return ""
    root = Path(folder)
    if not root.is_dir():
        msg = f"The backup folder {folder} is not available right now (is Google Drive running?). It will try again."
        _save(conn, msg, False)
        return msg
    try:
        dest = root / SUBFOLDER
        (dest / "database").mkdir(parents=True, exist_ok=True)
        snap = make_backup(conn, home, reason)                         # a consistent snapshot, kept locally too
        shutil.copy2(snap, dest / "database" / snap.name)
        old = sorted((dest / "database").glob("finance_*.db"))
        for f in old[:-KEEP_DB]:
            f.unlink(missing_ok=True)
        copied = 0
        for src in home.archive.rglob("*"):
            if not src.is_file() or src.name.startswith(".") or "_duplicates" in src.parts:
                continue
            target = dest / "statements" / src.relative_to(home.archive)
            if target.exists() and target.stat().st_size == src.stat().st_size:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            copied += 1
    except OSError as exc:
        msg = f"The backup did not finish ({exc}). It will try again."
        _save(conn, msg, False)
        return msg
    msg = f"Backed up the database" + (f" and {copied} statement file(s)" if copied else "") + f" to {dest}"
    _save(conn, msg, True)
    return msg


def after_import(conn: sqlite3.Connection, home: Home) -> None:
    """Called after statements were imported: put them in the backup folder straight away. Never breaks the import."""
    try:
        if get(conn):
            run(conn, home, "after-import")
    except Exception:
        pass


def maybe_run(conn: sqlite3.Connection, home: Home) -> str:
    """Back up when something changed since the last good backup and it is at least a few hours old."""
    if not get(conn):
        return ""
    last = _last_ok(conn)
    if time.time() - last < EVERY_SECONDS:
        return ""
    stamps = [p.stat().st_mtime for p in (home.db_path, home.db_path.with_name(home.db_path.name + "-wal")) if p.exists()]
    if last and stamps and max(stamps) <= last:
        return ""
    return run(conn, home, "scheduled")


def available(conn: sqlite3.Connection, home: Home) -> list[dict]:
    """Backups you can restore from, newest first: the ones kept on this computer and the ones in the backup folder."""
    out = []
    folders = [("this computer", home.backups)]
    f = get(conn)
    if f:
        folders.append(("backup folder", Path(f) / SUBFOLDER / "database"))
    for label, d in folders:
        if not d.is_dir():
            continue
        for p in d.glob("finance_*.db"):
            out.append({"where": label, "name": p.name, "path": p, "size": p.stat().st_size,
                        "when": datetime.fromtimestamp(p.stat().st_mtime).strftime("%d/%m/%Y %H:%M")})
    out.sort(key=lambda r: r["path"].stat().st_mtime, reverse=True)
    return out[:60]


def restore(conn: sqlite3.Connection, home: Home, where: str, name: str) -> str:
    """Replace the current data with a chosen backup. The current data is saved first, so this can be undone."""
    pick = next((b for b in available(conn, home) if b["where"] == where and b["name"] == name), None)
    if not pick:
        raise ValueError("That backup was not found.")
    probe = sqlite3.connect(f"file:{pick['path']}?mode=ro", uri=True)
    try:
        if probe.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or not probe.execute("SELECT 1 FROM sqlite_master WHERE name = 'transactions'").fetchone():
            raise ValueError("That backup file is damaged or is not a finance backup.")
    finally:
        probe.close()
    make_backup(conn, home, "before-restore")
    src = sqlite3.connect(pick["path"])
    try:
        src.backup(conn)
    finally:
        src.close()
    conn.commit()
    return f"Restored the data from {pick['when']}. What you had before is kept as a backup named …before-restore."
