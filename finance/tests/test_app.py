import json
import threading
import time
from datetime import date
from urllib.parse import urlencode

import pytest

from ftracker import accounts, appmode, importer, starter, uploads
from ftracker.web import App
from .helpers import card_file, card_txn
from .test_sheet_import import _workbook

H = {"host": "localhost:8765"}
BOUNDARY = "----finboundary123"


def multipart(files, boundary=BOUNDARY):
    body = b""
    for name, data in files:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                 "Content-Type: application/octet-stream\r\n\r\n").encode("utf-8") + data + b"\r\n"
    return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


@pytest.fixture
def app(home, conn, tmp_path):
    accounts.add_owner(conn, "Ely"); accounts.add_owner(conn, "Shir")
    conn.commit()
    return App(home, today="2026-10-05", app_dir=tmp_path / "appdir")


def upload(app, path, files, csrf=None):
    body, ctype = multipart(files)
    return app.handle("POST", path, {**H, "content-type": ctype, "x-csrf": app.csrf if csrf is None else csrf}, body)


def post(app, path, data):
    return app.handle("POST", path, H, urlencode({"csrf": app.csrf, **data}, doseq=True).encode())


def flash(res):
    from urllib.parse import unquote
    return unquote(next(v for k, v in res.headers if k == "Set-Cookie" and v.startswith("flash=")).split(";")[0][6:])


# ---- running as an app ---------------------------------------------------------------------------

def test_edge_is_preferred_and_opens_as_an_app_window_else_the_default_browser():
    env = {"ProgramFiles(x86)": r"C:\Pf86"}
    exe = r"C:\Pf86\Microsoft\Edge\Application\msedge.exe".replace("\\", "/")
    found = appmode.find_browser(env, which=lambda n: None, exists=lambda p: p.replace("\\", "/") == exe or p == r"C:\Pf86\Microsoft\Edge\Application\msedge.exe")
    assert found and found.endswith("msedge.exe")
    calls = []
    assert appmode.open_window("http://localhost:1/", popen=lambda cmd: calls.append(cmd), browser="/x/msedge") == "app-window"
    assert calls[0][:2] == ["/x/msedge", "--app=http://localhost:1/"]
    opened = []
    assert appmode.open_window("http://localhost:1/", web_open=opened.append, browser=None) == "browser" and opened == ["http://localhost:1/"]
    def boom(cmd):
        raise OSError("cannot start")
    assert appmode.open_window("http://localhost:1/", popen=boom, web_open=opened.append, browser="/x/msedge") == "browser"


def test_the_app_stops_itself_only_when_the_window_is_gone_and_nothing_is_running():
    w = appmode.Watchdog(started=1000, idle=180, never=900)
    assert not w.should_stop(1500)                     # window still opening
    assert w.should_stop(1901)                         # never opened at all
    w.ping(2000)
    assert not w.should_stop(2100) and w.should_stop(2181)
    assert not w.should_stop(5000, active_requests=1)  # never in the middle of an import


def test_port_choice_and_single_instance_detection():
    assert appmode.pick_port(8765, is_free=lambda p: p >= 8767) == 8767
    with pytest.raises(OSError):
        appmode.pick_port(8765, is_free=lambda p: False, span=3)
    class Resp:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return self.body
    assert appmode.is_ours(1, opener=lambda *a, **k: Resp(b'{"app": "finance-tracker"}'))
    assert not appmode.is_ours(1, opener=lambda *a, **k: Resp(b'{"app": "something else"}'))
    def refused(*a, **k):
        raise OSError("refused")
    assert not appmode.is_ours(1, opener=refused)


def test_ping_keeps_the_app_alive_and_quit_stops_it(home, conn, tmp_path):
    clock = [100.0]
    wd = appmode.Watchdog(started=100.0)
    app = App(home, watchdog=wd, clock=lambda: clock[0], app_dir=tmp_path)
    res = app.handle("GET", "/ping", H)
    assert json.loads(res.body) == {"app": "finance-tracker", "ok": True} and wd.last_ping == 100.0
    done = threading.Event()
    app.shutdown = done.set
    page = app.handle("GET", "/", H).body.decode()
    assert "/ping" in page and "Quit" in page                                # every page keeps the heartbeat going
    assert app.handle("POST", "/quit", H, b"csrf=wrong").status == 403 and not done.is_set()
    assert post(app, "/quit", {}).status == 200
    assert done.wait(3)


# ---- dropping files --------------------------------------------------------------------------------

def test_multipart_parsing_keeps_hebrew_names_and_strips_folders():
    body, ctype = multipart([("דוח אשראי 1111.xlsx", b"abc"), ("..\\..\\evil/../x.pdf", b"def")])
    files = uploads.parse_multipart(ctype, body)
    assert [f.name for f in files] == ["דוח אשראי 1111.xlsx", "x.pdf"] and files[0].data == b"abc"
    assert uploads.safe_name("a/b\\c:d?.xlsx") == "c_d_.xlsx" and uploads.safe_name("...") == "file"
    with pytest.raises(uploads.UploadError):
        uploads.parse_multipart("application/json", b"{}")


def test_upload_saves_only_statement_files_and_never_overwrites(app, home):
    r = upload(app, "/upload", [("a.xlsx", b"1"), ("a.xlsx", b"2"), ("virus.exe", b"x"), ("empty.pdf", b"")])
    j = json.loads(r.body)
    assert sorted(j["saved"]) == ["a-2.xlsx", "a.xlsx"] and len(j["rejected"]) == 2
    assert (home.inbox / "a.xlsx").read_bytes() == b"1" and (home.inbox / "a-2.xlsx").read_bytes() == b"2"


def test_upload_needs_the_page_token_and_the_same_origin(app, home):
    assert upload(app, "/upload", [("a.xlsx", b"1")], csrf="nope").status == 403
    body, ctype = multipart([("a.xlsx", b"1")])
    assert app.handle("POST", "/upload", {**H, "content-type": ctype, "x-csrf": app.csrf, "origin": "http://evil.example"}, body).status == 403
    assert not list(home.inbox.glob("*.xlsx"))


def test_drop_then_import_works_end_to_end_with_no_folders(app, home, monkeypatch):
    parsed = card_file(txns=[card_txn(date(2026, 9, 3), "SUPER", 100.0, "v1")])
    monkeypatch.setattr(importer, "parse_file", lambda path: parsed)
    assert json.loads(upload(app, "/upload", [("statement.xlsx", b"bytes")]).body)["saved"] == ["statement.xlsx"]
    res = post(app, "/import", {})
    assert "1 file(s) imported" in flash(res)
    assert not list(home.inbox.glob("*.xlsx"))                                   # filed away automatically
    assert list((home.archive / "credit-cards").rglob("*.xlsx"))
    assert "Drop statements here" in app.handle("GET", "/imports", H).body.decode()


# ---- old sheet in the browser ------------------------------------------------------------------------

def test_old_sheet_preview_mapping_and_save_through_the_browser_flow(app, home, tmp_path):
    xlsx = _workbook(tmp_path / "h.xlsx").read_bytes()
    assert "Choose the downloaded" in app.handle("GET", "/history", H).body.decode() or "Excel" in app.handle("GET", "/history", H).body.decode()
    bad = json.loads(upload(app, "/history/upload", [("notes.txt", b"x")]).body)
    assert "error" in bad
    broken = json.loads(upload(app, "/history/upload", [("x.xlsx", b"not excel")]).body)
    assert "could not be read" in broken["error"] and not list((home.data / "tmp").glob("history-*"))
    token = json.loads(upload(app, "/history/upload", [("budget.xlsx", xlsx)]).body)["token"]
    page = app.handle("GET", f"/history?t={token}", H).body.decode()
    assert "Preview of the old sheet" in page and "Vituri" in page and "2025-03" in page
    res = post(app, "/history/save", {"t": token, "n_maps": "1", "src_0": "Vituri", "dst_0": "Medical", "primary": "Ely", "partner": "Shir"})
    assert "saved as draft" in flash(res)
    from ftracker.db import connect
    conn = connect(home.db_path)
    cat = conn.execute("SELECT c.name FROM transactions t JOIN categories c ON c.id = t.category_id WHERE t.description = 'NEW CATEGORY'").fetchone()[0]
    assert cat == "Medical"                                                      # merged, not a stray "Vituri"
    assert not list((home.data / "tmp").glob("history-*"))
    assert "already imported" in app.handle("GET", "/history", H).body.decode()
    again = json.loads(upload(app, "/history/upload", [("budget.xlsx", xlsx)]).body)["token"]
    assert "Nothing new" in flash(post(app, "/history/save", {"t": again, "n_maps": "0", "primary": "Ely", "partner": "Shir"}))


def test_preview_tokens_cannot_be_used_to_reach_other_files(app):
    for t in ("../../etc/passwd", "deadbeef", "x" * 16, ""):
        res = app.handle("GET", f"/history?t={t}", H)
        assert res.status in (200, 303)
        assert b"/bin/" not in res.body and b"daemon:" not in res.body
    assert app.handle("GET", "/history?t=0123456789abcdef", H).status == 303      # unknown token: back to the upload page
    assert "expired" in flash(post(app, "/history/save", {"t": "../../x", "n_maps": "0"}))


def test_old_sheet_needs_a_person_first(home, conn, tmp_path):
    app = App(home, today="2026-10-05", app_dir=tmp_path)
    token = json.loads(upload(app, "/history/upload", [("b.xlsx", _workbook(tmp_path / "h.xlsx").read_bytes())]).body)["token"]
    assert "Add the people first" in app.handle("GET", f"/history?t={token}", H).body.decode()


# ---- setup page ---------------------------------------------------------------------------------------

def test_setup_page_people_names_and_account_owners(app, home):
    assert "Setup" in app.handle("GET", "/setup", H).body.decode()
    assert post(app, "/setup/owner", {"name": "Noa"}).status == 303
    post(app, "/setup/alias", {"owner": "Shir", "alias": "שיר"})
    from ftracker.db import connect
    conn = connect(home.db_path)
    alias_id = conn.execute("SELECT id FROM owner_aliases").fetchone()[0]
    assert "שיר" in app.handle("GET", "/setup", H).body.decode()
    assert "type the name exactly" in flash(post(app, "/setup/alias", {"owner": "Shir", "alias": "  "}))
    post(app, "/setup/alias/remove", {"id": str(alias_id)})
    assert conn.execute("SELECT COUNT(*) FROM owner_aliases").fetchone()[0] == 0
    accounts.add_account(conn, "savings", "x", "Rainy day"); conn.commit()
    aid = conn.execute("SELECT id FROM accounts WHERE label = 'Rainy day'").fetchone()[0]
    post(app, "/setup/account-owner", {"id": str(aid), "owner": "Shir"})
    assert conn.execute("SELECT o.name FROM accounts a JOIN owners o ON o.id = a.owner_id WHERE a.id = ?", (aid,)).fetchone()[0] == "Shir"


def test_setup_rules_of_every_kind_and_safe_deletion(app, home):
    from ftracker.db import connect
    conn = connect(home.db_path)
    accounts.add_account(conn, "savings", "x", "One Zero", "Ely"); conn.commit()
    one_zero = conn.execute("SELECT id FROM accounts WHERE label = 'One Zero'").fetchone()[0]
    post(app, "/setup/rule", {"pattern": "פאפאיה", "mode": "income", "category": "Salary"})
    post(app, "/setup/rule", {"pattern": "CORNER CAFE", "mode": "spend", "category": "Eating out"})
    post(app, "/setup/rule", {"pattern": "FAMILY BIT", "mode": "transfer_household", "category": "Fun"})
    post(app, "/setup/rule", {"pattern": "העברה דיגיטל", "mode": "transfer_account", "category": "Fun", "destination": str(one_zero)})
    rows = {r["pattern"]: r for r in conn.execute("SELECT * FROM rules WHERE source = 'user'")}
    assert rows["פאפאיה"]["set_kind"] == "income" and rows["פאפאיה"]["direction"] == "in"
    assert rows["FAMILY BIT"]["set_kind"] == "transfer"
    assert rows["העברה דיגיטל"]["counterparty_account_id"] == one_zero
    assert "type the words" in flash(post(app, "/setup/rule", {"pattern": " ", "mode": "spend", "category": "Fun"}))
    assert "choose the account" in flash(post(app, "/setup/rule", {"pattern": "X", "mode": "transfer_account", "category": "Fun"}))
    page = app.handle("GET", "/setup", H).body.decode()
    assert "CORNER CAFE" in page and "Built in" in page and "Learned from your history" in page
    rid = rows["CORNER CAFE"]["id"]
    post(app, "/setup/rule/toggle", {"id": str(rid)})
    assert conn.execute("SELECT enabled FROM rules WHERE id = ?", (rid,)).fetchone()[0] == 0
    post(app, "/setup/rule/delete", {"id": str(rid)})
    assert conn.execute("SELECT COUNT(*) FROM rules WHERE id = ?", (rid,)).fetchone()[0] == 0
    builtin = conn.execute("SELECT id FROM rules WHERE source = 'builtin' LIMIT 1").fetchone()[0]
    assert "not deleted" in flash(post(app, "/setup/rule/delete", {"id": str(builtin)}))


def test_starter_setup_is_a_file_next_to_the_app_applied_with_one_click_and_safe_to_repeat(home, conn, tmp_path):
    appdir = tmp_path / "appdir"; appdir.mkdir()
    app = App(home, today="2026-10-05", app_dir=appdir)
    assert "starter setup is ready" not in app.handle("GET", "/setup", H).body.decode()
    (appdir / starter.FILENAME).write_text(json.dumps({
        "owners": [{"name": "Ely"}, {"name": "Shir", "aliases": ["שיר"]}],
        "accounts": [{"label": "One Zero", "kind": "savings", "owner": "Ely", "issuer": "onezero"}],
        "rules": [{"pattern": "פאפאיה", "category": "Salary", "kind": "income", "direction": "in"}],
        "destination_rules": [{"pattern": "העברה דיגיטל", "account": "One Zero"}]}, ensure_ascii=False), encoding="utf-8")
    assert "starter setup is ready" in app.handle("GET", "/setup", H).body.decode()
    for _ in range(2):
        assert "Starter setup applied" in flash(post(app, "/setup/starter", {}))
    from ftracker.db import connect
    c2 = connect(home.db_path)
    assert c2.execute("SELECT COUNT(*) FROM accounts WHERE label = 'One Zero'").fetchone()[0] == 1
    assert c2.execute("SELECT COUNT(*) FROM rules WHERE source = 'user'").fetchone()[0] == 2     # the income rule and the destination rule, once each
    assert c2.execute("SELECT COUNT(*) FROM owner_aliases").fetchone()[0] == 1


def test_desktop_shortcut_is_windows_only_and_reports_clearly_elsewhere(app):
    if appmode.WINDOWS:
        pytest.skip("covered by manual test on Windows")
    with pytest.raises(OSError):
        appmode.create_desktop_shortcut("x", "y", "z")
