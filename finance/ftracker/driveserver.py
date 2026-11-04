"""Google Drive for a server that has no Drive app: rclone copies the Drive folders to and from folders on the server.

Statements you drop into your Drive folder are copied to a local folder (the app's watch folder), imported, and
the nightly database backup is copied back to Drive. rclone is set up once, with your own Google sign-in, and the
app only ever calls it. Nothing is deleted on either side: this only copies.
"""
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import date
from pathlib import Path

from . import cloudbackup, watchfolder
from .config import Home

IN_KEY, OUT_KEY, RESULT_KEY = "rclone_in", "rclone_out", "drive_server_result"
INCLUDE = ("*.xlsx", "*.xls", "*.pdf", "*.jpg", "*.jpeg", "*.png", "*.heic", "*.webp")
POLL_SECONDS = 10 * 60
TIMEOUT = 600


def _get(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row and row[0] else None


def _set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def in_dir(home: Home) -> Path:
    return home.root / "drive-in"


def out_dir(home: Home) -> Path:
    return home.root / "drive-out"


def configure(conn: sqlite3.Connection, home: Home, drive_in: str | None, drive_out: str | None) -> None:
    """Remember the two Drive folders and point the watch folder and the backup folder at their local copies."""
    if drive_in:
        _set(conn, IN_KEY, drive_in)
        in_dir(home).mkdir(parents=True, exist_ok=True)
        _set(conn, watchfolder.KEY, str(in_dir(home).resolve()))
    if drive_out:
        _set(conn, OUT_KEY, drive_out)
        out_dir(home).mkdir(parents=True, exist_ok=True)
        _set(conn, cloudbackup.KEY, str(out_dir(home).resolve()))
    conn.commit()


def available() -> bool:
    return shutil.which("rclone") is not None


def _run(cmd: list[str], runner=subprocess.run) -> tuple[bool, str]:
    try:
        r = runner(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        return False, "rclone is not installed on this server."
    except subprocess.TimeoutExpired:
        return False, "Google Drive did not answer in time."
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "rclone failed").strip().splitlines()[-1][:300]
    return True, ""


def pull(conn: sqlite3.Connection, home: Home, runner=subprocess.run) -> tuple[bool, str]:
    remote = _get(conn, IN_KEY)
    if not remote:
        return True, ""
    cmd = ["rclone", "copy", remote, str(in_dir(home))]
    for pat in INCLUDE:
        cmd += ["--include", pat]
    return _run(cmd, runner)


def push(conn: sqlite3.Connection, home: Home, runner=subprocess.run) -> tuple[bool, str]:
    remote = _get(conn, OUT_KEY)
    src = out_dir(home) / cloudbackup.SUBFOLDER
    if not remote or not src.is_dir():
        return True, ""
    return _run(["rclone", "copy", str(src), remote.rstrip("/") + "/" + cloudbackup.SUBFOLDER], runner)


def cycle(conn: sqlite3.Connection, home: Home, today: date, runner=subprocess.run, force: bool = False) -> str:
    """One round: fetch new statements from Drive, import them, back up if due, send the backup to Drive."""
    parts = []
    ok, err = pull(conn, home, runner)
    if not ok:
        parts.append("Could not read your Drive folder: " + err)
    note = watchfolder.scan(conn, home, today, force=force)
    if note:
        parts.append(note)
    backup = cloudbackup.maybe_run(conn, home)
    if backup:
        parts.append(backup)
    ok, err = push(conn, home, runner)
    if not ok:
        parts.append("Could not copy the backup to Drive: " + err)
    msg = "; ".join(parts)
    if msg:
        _set(conn, RESULT_KEY, f"{msg} · {time.strftime('%Y-%m-%d %H:%M')}")
        conn.commit()
    return msg


def last_result(conn) -> str:
    return _get(conn, RESULT_KEY) or ""


def start_background(home: Home, connect, today=lambda: date.today(), every: int = POLL_SECONDS, runner=subprocess.run):
    """A thread that runs a round every few minutes. Returns (thread, stop_event)."""
    stop = threading.Event()

    def loop():
        stop.wait(20)                                   # let the app finish starting
        while not stop.is_set():
            c = connect(home.db_path)
            try:
                cycle(c, home, today(), runner)
            except Exception as exc:                    # one bad round must not end the loop
                from . import errorlog
                errorlog.record(home, "drive round", exc)
            finally:
                c.close()
            stop.wait(every)
    t = threading.Thread(target=loop, daemon=True, name="drive-rounds")
    t.start()
    return t, stop
