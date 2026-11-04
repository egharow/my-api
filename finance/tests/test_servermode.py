from urllib.parse import urlencode

import pytest

from ftracker import accounts, servermode
from ftracker.web import App

HOST = "finance.example.ts.net"


def _hdr(extra=None):
    return {"host": HOST, "x-forwarded-proto": "https", **(extra or {})}


@pytest.fixture
def srv(home, conn):
    accounts.add_owner(conn, "Ely"); accounts.add_owner(conn, "Shir")
    servermode.add_user(conn, "Ely", "correct horse battery")
    conn.commit()
    t = [1000.0]
    app = App(home, today="2026-10-05", server=True, allow_hosts=(HOST,), clock=lambda: t[0])
    app.t = t
    return app


def login(app, name="Ely", pw="correct horse battery", headers=None):
    body = urlencode({"user": name, "password": pw}).encode()
    return app.handle("GET" if False else "POST", "/login", _hdr(headers), body)


def cookie_of(res):
    for k, v in res.headers:
        if k == "Set-Cookie" and v.startswith("session="):
            return v.split(";")[0]
    return None


def test_password_hashing_and_sessions():
    h = servermode.hash_password("a long enough password")
    assert servermode.verify_password("a long enough password", h)
    assert not servermode.verify_password("another one entirely", h)
    assert not servermode.verify_password("anything", None)
    key = b"k" * 32
    tok = servermode.make_session(key, "Ely", 100.0)
    assert servermode.read_session(key, tok, 200.0) == "Ely"
    assert servermode.read_session(key, tok, 100.0 + servermode.SESSION_SECONDS + 1) is None      # expired
    assert servermode.read_session(b"x" * 32, tok, 200.0) is None                                  # signed with another key
    assert servermode.read_session(key, tok[:-2] + "00", 200.0) is None                            # tampered


def test_short_passwords_and_odd_names_are_refused(conn):
    with pytest.raises(ValueError):
        servermode.add_user(conn, "Ely", "short")
    with pytest.raises(ValueError):
        servermode.add_user(conn, "E|ly", "long enough password")


def test_everything_needs_a_sign_in(srv):
    for path in ("/", "/review", "/transactions", "/wealth"):
        assert srv.handle("GET", path, _hdr()).status == 401
    assert srv.handle("GET", "/login", _hdr()).status == 200
    assert srv.handle("GET", "/health", _hdr()).status == 200            # the launcher check stays open
    assert srv.handle("GET", "/", {"host": "evil.example", "x-forwarded-proto": "https"}).status == 421


def test_sign_in_opens_the_whole_app_and_marks_changes_as_yours(srv):
    res = login(srv)
    assert res.status == 303 and "Secure" in dict(res.headers)["Set-Cookie"] and "HttpOnly" in dict(res.headers)["Set-Cookie"]
    ck = cookie_of(res)
    page = srv.handle("GET", "/transactions?m=all", _hdr({"cookie": ck}))
    assert page.status == 200
    assert "Signed in as Ely" in page.body.decode() and "Sign out" in page.body.decode() and ">Quit<" not in page.body.decode()
    assert srv.handle("GET", "/review", _hdr({"cookie": ck})).status == 200          # not view-only, unlike the home-network phone mode


def test_wrong_password_then_lockout_then_recovery(srv):
    for _ in range(5):
        assert login(srv, pw="wrong wrong wrong").status == 403
    assert login(srv).status == 429                                      # even the right password waits
    srv.t[0] += servermode.LOCK_SECONDS + 1
    assert login(srv).status == 303


def test_unknown_name_is_just_a_wrong_password(srv):
    r = login(srv, name="Nobody")
    assert r.status == 403 and "Wrong name or password" in r.body.decode()


def test_sign_out_and_removed_people_lose_access(srv, conn):
    ck = cookie_of(login(srv))
    out = srv.handle("POST", "/logout", _hdr({"cookie": ck}))
    assert out.status == 303 and "Max-Age=0" in dict(out.headers)["Set-Cookie"]
    servermode.remove_user(conn, "Ely")
    assert srv.handle("GET", "/", _hdr({"cookie": ck})).status == 401


def test_the_server_does_not_quit_from_the_page(srv):
    ck = cookie_of(login(srv))
    page = srv.handle("GET", "/", _hdr({"cookie": ck}))
    import re
    csrf = srv.csrf
    res = srv.handle("POST", "/quit", _hdr({"cookie": ck}), urlencode({"csrf": csrf}).encode())
    assert res.status == 303                                              # sent back to the page, nothing closed
    assert page.status == 200


def test_login_rejects_a_post_from_another_site(srv):
    body = urlencode({"user": "Ely", "password": "correct horse battery"}).encode()
    res = srv.handle("POST", "/login", _hdr({"origin": "https://evil.example"}), body)
    assert res.status == 403 and cookie_of(res) is None
