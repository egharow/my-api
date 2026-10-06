import sqlite3
from datetime import datetime
from importlib import resources
from pathlib import Path

# Version 1 is schema.sql as first released. Later changes are migrations, applied in order,
# so a database made by an earlier build is upgraded in place and keeps its data.
MIGRATIONS = [(2, "002_monthly_entries.sql"), (3, "003_goals.sql"), (4, "004_member_card.sql"), (5, "005_drop_card_rates.sql"), (6, "006_reimbursed.sql"), (7, "007_wealth_filter.sql")]
SCHEMA_VERSION = MIGRATIONS[-1][0]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    version = opened_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        conn.executescript(resources.files("ftracker").joinpath("schema.sql").read_text(encoding="utf-8"))
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        from . import seed
        seed.seed_all(conn)
        version = 1
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"database is newer (v{version}) than this app (v{SCHEMA_VERSION})")
    for target, name in MIGRATIONS:
        if version < target:
            if opened_version >= 1:   # only an existing database needs protecting, not a fresh one
                _backup_before_migration(conn, db_path, version)
            sql = resources.files("ftracker").joinpath("migrations", name).read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {target}")
            conn.commit()
            version = target
    return conn


def audit(conn: sqlite3.Connection, action: str, entity: str | None = None,
          entity_id: int | None = None, detail: str | None = None, actor: str = "user") -> None:
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?)",
        (now(), actor, action, entity, entity_id, detail),
    )


def _backup_before_migration(conn: sqlite3.Connection, db_path: Path, version: int) -> None:
    conn.commit()
    dest = db_path.with_name(f"{db_path.stem}.before-upgrade-v{version}.db")
    if not dest.exists():
        target = sqlite3.connect(dest)
        try:
            conn.backup(target)
        finally:
            target.close()
