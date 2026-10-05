import sqlite3
from datetime import datetime
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = 1


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        conn.executescript(resources.files("ftracker").joinpath("schema.sql").read_text(encoding="utf-8"))
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        from . import seed
        seed.seed_all(conn)
    elif version > SCHEMA_VERSION:
        raise RuntimeError(f"database is newer (v{version}) than this app (v{SCHEMA_VERSION})")
    return conn


def audit(conn: sqlite3.Connection, action: str, entity: str | None = None,
          entity_id: int | None = None, detail: str | None = None, actor: str = "user") -> None:
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?)",
        (now(), actor, action, entity, entity_id, detail),
    )
