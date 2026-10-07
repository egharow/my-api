import os
import time
from datetime import date

import pytest

from ftracker import importer, watchfolder
from .helpers import card_file


def _age(path, seconds=60):
    t = time.time() - seconds
    os.utime(path, (t, t))


@pytest.fixture
def drive(tmp_path, monkeypatch):
    d = tmp_path / "Drive"; d.mkdir()
    parsed = {"isra.xlsx": card_file(issuer="isracard", last4="6097")}
    monkeypatch.setattr(importer, "parse_file", lambda path: parsed[path.name.replace("-1", "")])
    watchfolder._last_scan["t"] = 0.0
    return d


def test_new_statements_are_picked_up_once(conn, home, drive):
    f = drive / "isra.xlsx"; f.write_bytes(b"statement-1"); _age(f)
    watchfolder.set_folder(conn, home, str(drive))
    msg = watchfolder.scan(conn, home, date(2026, 10, 5), force=True)
    assert "1 new statement(s) imported" in msg
    assert f.exists()                                                       # the original stays in Drive
    assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 1
    assert watchfolder.scan(conn, home, date(2026, 10, 5), force=True) == ""   # nothing is added twice
    assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 1


def test_files_still_syncing_and_other_types_are_left_alone(conn, home, drive):
    (drive / "isra.xlsx").write_bytes(b"just-arrived")                      # modified a moment ago
    (drive / "notes.txt").write_bytes(b"x"); _age(drive / "notes.txt")
    watchfolder.set_folder(conn, home, str(drive))
    assert watchfolder.scan(conn, home, date(2026, 10, 5), force=True) == ""
    assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 0


def test_bad_folders_are_refused(conn, home, tmp_path):
    with pytest.raises(ValueError):
        watchfolder.set_folder(conn, home, str(tmp_path / "missing"))
    home.ensure()
    with pytest.raises(ValueError):
        watchfolder.set_folder(conn, home, str(home.inbox))
