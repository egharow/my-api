import sqlite3
from datetime import datetime
from pathlib import Path

from .config import Home


def make_backup(conn: sqlite3.Connection, home: Home, tag: str) -> Path:
    home.backups.mkdir(parents=True, exist_ok=True)
    conn.commit()  # a backup of a connection with pending writes would block forever
    dest = home.backups / f"finance_{datetime.now():%Y%m%d-%H%M%S}_{tag}.db"
    target = sqlite3.connect(dest)
    try:
        conn.backup(target)
    finally:
        target.close()
    return dest


def prune(home: Home, keep: int = 30) -> None:
    files = sorted(home.backups.glob("finance_*_pre-import.db"))
    for old in files[:-keep]:
        old.unlink(missing_ok=True)
