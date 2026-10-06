import io
import json
import shutil
import subprocess
from datetime import date
from urllib.parse import urlencode

import pytest

from ftracker import accounts, balances, commits, fx, sheetsync
from ftracker.web import App
from .helpers import card_file, card_txn, run_import

URL = "https://script.google.com/macros/s/AKfycbxEXAMPLE/exec"


class FakeGoogle:
    def __init__(self, answer=None, raw=None, offline=False):
        self.answer, self.raw, self.offline, self.requests = answer or {"ok": True, "sheets": 6}, raw, offline, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if self.offline:
            raise OSError("no network")
        return io.BytesIO((self.raw if self.raw is not None else json.dumps(self.answer)).encode())

    @property
    def sent(self):
        return json.loads(self.requests[-1].data)


@pytest.fixture
def linked(conn):
    sheetsync.link(conn, URL)
    return conn


def _submitted_import(home, conn, monkeypatch, billing=date(2026, 9, 2)):
    accounts.add_owner(conn, "Ely")
    run_import(home, conn, monkeypatch, [("c.xlsx", card_file(billing=billing, txns=[card_txn(date(2026, 8, 20), "SUPER", 100.0, "v")]))])
    return commits.submit(conn, home, 1, acknowledge=True, today="2026-10-05")


def test_script_carries_a_stable_secret_and_resets_on_request(conn):
    first = sheetsync.script_text(conn)
    assert "__TOKEN__" not in first and sheetsync.token(conn) in first
    assert sheetsync.script_text(conn) == first
    assert sheetsync.script_text(conn, reset=True) != first


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_script_is_valid_javascript(conn, tmp_path):
    f = tmp_path / "Code.gs.js"
    f.write_text(sheetsync.script_text(conn), encoding="utf-8")
    assert subprocess.run(["node", "--check", str(f)], capture_output=True).returncode == 0


@pytest.mark.parametrize("url,ok", [(URL, True), ("https://script.google.com/macros/s/x/dev", False), ("http://script.google.com/macros/s/x/exec", False),
                                    ("https://evil.example/macros/s/x/exec", False), (URL + "?x=1", False), ("", False)])
def test_only_a_real_web_app_address_is_accepted(conn, url, ok):
    assert sheetsync.valid_url(url) is ok
    if not ok:
        with pytest.raises(ValueError):
            sheetsync.link(conn, url)


def test_push_sends_the_secret_and_only_submitted_numbers(home, linked, monkeypatch):
    conn = linked
    _submitted_import(home, conn, monkeypatch)
    g = FakeGoogle()
    res = sheetsync.push(conn, "2026-10-05", g)
    assert res["ok"] and "6 sheet tab" in res["detail"]
    req = g.requests[0]
    assert req.full_url == URL and req.get_method() == "POST"
    sent = g.sent
    assert sent["token"] == sheetsync.token(conn)
    tabs = {t["name"]: t for t in sent["sheets"]}
    assert {"Info", "Monthly summary", "By category", "Net worth", "Accounts", "Goals"} <= set(tabs)
    assert "Notes" not in tabs                                           # private unless you opt in
    assert tabs["Monthly summary"]["rows"][0][0] == "2026-09" and tabs["Monthly summary"]["rows"][0][2] == 100.0
    st = sheetsync.status(conn)
    assert st["linked"] and st["last_sync"] and st["last_result"].startswith("ok")


def test_drafts_never_reach_the_sheet(home, linked, monkeypatch):
    conn = linked
    accounts.add_owner(conn, "Ely")
    run_import(home, conn, monkeypatch, [("c.xlsx", card_file())])      # imported but not submitted
    g = FakeGoogle()
    sheetsync.push(conn, "2026-10-05", g)
    tabs = {t["name"]: t for t in g.sent["sheets"]}
    assert tabs["Monthly summary"]["rows"] == [] and dict(tabs["Info"]["rows"])["Draft imports not included"] == 1


def test_comments_are_sent_only_when_you_turn_them_on(home, linked, monkeypatch):
    conn = linked
    _submitted_import(home, conn, monkeypatch)
    from ftracker import discrepancies as dx
    dx.open_manual(conn, 1, "private remark"); conn.commit()
    g = FakeGoogle()
    sheetsync.push(conn, "2026-10-05", g)
    assert "private remark" not in json.dumps(g.sent)
    sheetsync.set_notes(conn, True)
    sheetsync.push(conn, "2026-10-05", g)
    assert "private remark" in json.dumps(g.sent, ensure_ascii=False)


@pytest.mark.parametrize("fake,fragment", [
    (FakeGoogle(offline=True), "retry on the next submit"),
    (FakeGoogle(raw="<html>Sign in</html>"), "Anyone"),
    (FakeGoogle(answer={"ok": False, "error": "wrong token"}), "different secret"),
    (FakeGoogle(answer={"ok": False, "error": "boom"}), "boom"),
])
def test_failures_are_explained_and_never_raise(linked, fake, fragment):
    res = sheetsync.push(linked, "2026-10-05", fake)
    assert res["ok"] is False and fragment in res["detail"]
    assert sheetsync.status(linked)["last_result"].startswith("failed")
    assert sheetsync.status(linked)["last_sync"] is None


def test_not_linked_means_no_sync_and_no_network(conn):
    g = FakeGoogle()
    assert sheetsync.sync_if_linked(conn, "2026-10-05", g) is None and not g.requests
    assert sheetsync.push(conn, "2026-10-05", g)["ok"] is False and not g.requests


def test_unlinking_forgets_the_address_but_keeps_the_secret(linked):
    t = sheetsync.token(linked)
    sheetsync.unlink(linked)
    assert not sheetsync.status(linked)["linked"] and sheetsync.token(linked) == t


# ---- through the web app -----------------------------------------------------------------------

H = {"host": "localhost:8765"}


def _post(app, path, data):
    return app.handle("POST", path, H, urlencode({"csrf": app.csrf, **data}).encode())


def _flash(res):
    from urllib.parse import unquote
    return unquote(next(v for k, v in res.headers if k == "Set-Cookie" and v.startswith("flash=")).split(";")[0][6:])


def test_submitting_in_the_app_updates_the_sheet_and_a_failure_does_not_undo_the_submit(home, conn, monkeypatch):
    import urllib.request
    accounts.add_owner(conn, "Ely")
    run_import(home, conn, monkeypatch, [("c.xlsx", card_file(txns=[card_txn(date(2026, 8, 20), "SUPER", 100.0, "v")]))])
    sheetsync.link(conn, URL)
    conn.commit()
    app = App(home, today="2026-10-05")
    good = FakeGoogle()
    monkeypatch.setattr(urllib.request, "urlopen", good)
    res = _post(app, "/imports/1/submit", {"ack": "1"})
    assert "submitted" in _flash(res) and "Google Sheet updated" in _flash(res) and good.requests
    _post(app, "/imports/1/reopen", {"reason": "typo"})
    monkeypatch.setattr(urllib.request, "urlopen", FakeGoogle(offline=True))
    res = _post(app, "/imports/1/submit", {"ack": "1"})
    assert _flash(res).startswith("err|") and "NOT updated" in _flash(res)
    from ftracker.db import connect
    assert connect(home.db_path).execute("SELECT status FROM batches WHERE id = 1").fetchone()[0] == "committed"


def test_sheet_page_guides_the_setup_and_validates_the_address(home, conn, monkeypatch):
    import urllib.request
    conn.commit()
    app = App(home, today="2026-10-05")
    page = app.handle("GET", "/sheet", H).body.decode()
    assert "Not linked yet" in page and "Extensions" in page and sheetsync.token(conn) in page
    assert "not linked yet" in app.handle("GET", "/dashboard", H).body.decode().lower()
    bad = _post(app, "/sheet/link", {"url": "https://example.com"})
    assert _flash(bad).startswith("err|")
    monkeypatch.setattr(urllib.request, "urlopen", FakeGoogle())
    ok = _post(app, "/sheet/link", {"url": URL})
    assert "Linked" in _flash(ok)
    assert "Linked" in app.handle("GET", "/sheet", H).body.decode()
    monkeypatch.setattr(urllib.request, "urlopen", FakeGoogle(raw="<html>"))
    assert "Not updated" in _flash(_post(app, "/sheet/sync", {}))
    assert "did not update" in app.handle("GET", "/", H).body.decode()
