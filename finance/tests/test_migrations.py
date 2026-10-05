import sqlite3
from importlib import resources

from ftracker import seed
from ftracker.db import SCHEMA_VERSION, connect


def _make_v1(path):
    c = sqlite3.connect(path)
    c.executescript(resources.files("ftracker").joinpath("schema.sql").read_text(encoding="utf-8"))
    c.execute("PRAGMA user_version = 1")
    seed.seed_all(c)
    c.execute("INSERT INTO owners (name) VALUES ('Ely')")
    c.commit()
    c.close()


def test_a_first_release_database_is_upgraded_in_place_and_backed_up(tmp_path):
    db = tmp_path / "finance.db"
    _make_v1(db)
    conn = connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert conn.execute("SELECT name FROM owners").fetchone()[0] == "Ely"          # data kept
    conn.execute("INSERT INTO monthly_entries (month, section, label, source, created_at) VALUES ('2025-01','income','x','t','now')")
    conn.execute("INSERT INTO goals (name, target_amount, created_at) VALUES ('g', 1, 'now')")
    assert (tmp_path / "finance.before-upgrade-v1.db").exists()
    conn.close()


def test_a_new_database_is_not_backed_up_and_reopening_is_a_no_op(tmp_path):
    db = tmp_path / "finance.db"
    connect(db).close()
    assert not list(tmp_path.glob("*.before-upgrade*"))
    connect(db).close()
    assert not list(tmp_path.glob("*.before-upgrade*"))


def test_a_database_from_the_middle_commit_that_already_has_monthly_entries_still_upgrades(tmp_path):
    db = tmp_path / "finance.db"
    _make_v1(db)
    c = sqlite3.connect(db)
    c.executescript(resources.files("ftracker").joinpath("migrations", "002_monthly_entries.sql").read_text(encoding="utf-8"))
    c.close()                                                  # version still 1, table already there
    conn = connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()
