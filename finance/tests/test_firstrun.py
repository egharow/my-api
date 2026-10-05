import json
import shutil
from datetime import date
from urllib.parse import urlencode

import pytest

from ftracker import accounts, firstrun, importer, sheet_import, starter
from ftracker.web import App
from .helpers import card_file, card_txn
from .test_sheet_import import _workbook

H = {"host": "localhost:8765"}


@pytest.fixture
def appdir(tmp_path):
    d = tmp_path / "appdir"
    (d / "seed" / "statements").mkdir(parents=True)
    _workbook(d / "seed" / "history.xlsx")
    (d / "seed" / "statements" / "card.xlsx").write_bytes(b"pretend statement")
    (d / starter.FILENAME).write_text(json.dumps({
        "owners": [{"name": "Ely", "aliases": ["אליקים"]}, {"name": "Shir", "aliases": ["שיר"]}],
        "accounts": [{"label": "One Zero", "kind": "savings", "owner": "Ely", "issuer": "onezero"}],
        "rules": [{"pattern": "פאפאיה", "category": "Salary", "kind": "income", "direction": "in"}],
        "destination_rules": [{"pattern": "העברה דיגיטל", "account": "One Zero"}]}, ensure_ascii=False), encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def stub_parser(monkeypatch):
    parsed = card_file(txns=[card_txn(date(2026, 9, 3), "SUPER", 100.0, "v1")])
    monkeypatch.setattr(importer, "parse_file", lambda path: parsed)


def test_everything_you_already_gave_is_loaded_in_one_go(conn, home, appdir):
    assert firstrun.needed(conn, appdir)
    out = firstrun.run(conn, home, appdir, date(2026, 10, 5))
    text = " | ".join(out["steps"])
    assert "2 people" in text and "old budget sheet: 10 lines" in text and "1 statement file(s) imported" in text
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 11            # 10 history lines + 1 statement line
    assert conn.execute("SELECT COUNT(*) FROM accounts WHERE label = 'One Zero'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM balances").fetchone()[0] > 0
    owner = conn.execute("SELECT o.name FROM accounts a JOIN owners o ON o.id = a.owner_id WHERE a.issuer = 'manual' AND a.label = 'שיר פנסיה כלל'").fetchone()[0]
    assert owner == "Shir"
    assert conn.execute("SELECT COUNT(*) FROM batches WHERE status = 'committed'").fetchone()[0] == 0   # nothing is submitted for you
    assert not firstrun.needed(conn, appdir) and not list(home.inbox.glob("*.xlsx"))


def test_it_runs_once_and_running_it_again_by_hand_adds_nothing(conn, home, appdir):
    firstrun.run(conn, home, appdir, date(2026, 10, 5))
    before = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    again = " | ".join(firstrun.run(conn, home, appdir, date(2026, 10, 5))["steps"])
    assert "already imported" in again and conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM rules WHERE source = 'user'").fetchone()[0] == 2


def test_a_data_folder_that_already_has_the_history_only_gets_what_is_missing(conn, home, appdir):
    accounts.add_owner(conn, "Ely"); accounts.add_owner(conn, "Shir")
    sheet_import.save(conn, sheet_import.parse_workbook(appdir / "seed" / "history.xlsx"))      # you did this step yourself earlier
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    text = " | ".join(firstrun.run(conn, home, appdir, date(2026, 10, 5))["steps"])
    assert "already imported" in text and "1 statement file(s) imported" in text
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == n + 1


def test_nothing_to_load_means_nothing_happens(conn, home, tmp_path):
    empty = tmp_path / "empty"; empty.mkdir()
    assert not firstrun.needed(conn, empty)


def test_a_broken_seed_file_is_reported_not_fatal(conn, home, appdir):
    (appdir / "seed" / "history.xlsx").write_bytes(b"corrupt")
    text = " | ".join(firstrun.run(conn, home, appdir, date(2026, 10, 5))["steps"])
    assert "could not be read" in text and "1 statement file(s) imported" in text


def test_the_dashboard_says_what_was_loaded_until_you_dismiss_it(conn, home, appdir):
    firstrun.run(conn, home, appdir, date(2026, 10, 5))
    app = App(home, today="2026-10-05", app_dir=appdir)
    page = app.handle("GET", "/", H).body.decode()
    assert "Your starting data is loaded" in page and "old budget sheet" in page
    res = app.handle("POST", "/seed/dismiss", H, urlencode({"csrf": app.csrf}).encode())
    assert res.status == 303
    assert "Your starting data is loaded" not in app.handle("GET", "/", H).body.decode()


def test_the_starter_file_can_carry_the_shared_sheet_and_its_secret(conn, home, tmp_path):
    from ftracker import sheetsync
    d = tmp_path / "ad"; d.mkdir()
    (d / starter.FILENAME).write_text(json.dumps({"owners": [{"name": "Ely"}], "sheet": {
        "doc_url": "https://docs.google.com/spreadsheets/d/abc/edit", "token": "t" * 32}}), encoding="utf-8")
    starter.apply(conn, d / starter.FILENAME)
    assert sheetsync.token(conn) == "t" * 32 and sheetsync.status(conn)["doc_url"].endswith("/abc/edit")
    assert "t" * 32 in sheetsync.script_text(conn)
    starter.apply(conn, d / starter.FILENAME)                                                  # twice changes nothing
    page = App(home, today="2026-10-05", app_dir=d).handle("GET", "/sheet", H).body.decode()
    assert "your shared sheet" in page and "docs.google.com/spreadsheets/d/abc" in page and "Copy the script" in page
