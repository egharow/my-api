import hashlib
import json
import time
from datetime import date, datetime

import pytest

from ftracker import accounts, cardfetch, importer, vault
from ftracker.parsers import cardfetch as reader
from ftracker.web import App
from .helpers import card_file, card_txn, run_import
from .test_web import get, post, text

SECRET = "s3cret-Pass!"


class FakeDpapi:
    """Stands in for Windows' encryption in tests: reversible, and refuses a different PIN like the real one."""

    def protect(self, data, entropy):
        return b"X" + hashlib.sha256(entropy or b"").digest()[:4] + bytes(b ^ 0x5A for b in data)

    def unprotect(self, data, entropy):
        if data[1:5] != hashlib.sha256(entropy or b"").digest()[:4]:
            raise vault.WrongPin("the saved logins could not be opened")
        return bytes(b ^ 0x5A for b in data[5:])


@pytest.fixture(autouse=True)
def secrets_dir(tmp_path, monkeypatch):
    d = tmp_path / "profile" / "FinanceTracker"
    monkeypatch.setenv("FTRACKER_SECRETS_DIR", str(d))
    monkeypatch.setattr(vault, "BACKEND", FakeDpapi())
    monkeypatch.setattr(vault, "PIN_ROUNDS", 1000)
    monkeypatch.setattr(cardfetch, "JOB", cardfetch.Job())
    return d


def fetched(txns, number="1234"):
    return {"ok": True, "accounts": [{"accountNumber": number, "txns": txns}]}


def site_txn(day, desc, amount, ident, billed="2026-10-02", status="completed", **kw):
    return {"type": "normal", "identifier": ident, "date": day, "processedDate": billed, "originalAmount": amount,
            "originalCurrency": "ILS", "chargedAmount": amount, "chargedCurrency": "ILS", "description": desc,
            "memo": "", "installments": None, "status": status, **kw}


# ---- the encrypted store ------------------------------------------------------------------------------------------

def test_logins_are_stored_encrypted_outside_the_app_and_data(home, secrets_dir):
    vault.save_login("max", "Ely", {"username": "ely1", "password": SECRET})
    raw = vault.path().read_bytes()
    assert vault.path().parent == secrets_dir
    assert SECRET.encode() not in raw and b"ely1" not in raw
    assert not str(vault.path()).startswith(str(home.root))
    assert vault.list_logins() == [{"id": "max:Ely", "company": "max", "person": "Ely", "saved_at": vault.list_logins()[0]["saved_at"]}]
    assert vault.load_login("max:Ely") == {"username": "ely1", "password": SECRET}


def test_fetch_pin_locks_the_logins_and_can_be_changed_or_removed():
    vault.save_login("visaCal", "Ely", {"username": "u", "password": SECRET})
    vault.set_pin(None, "4821")
    with pytest.raises(vault.WrongPin):
        vault.load_login("visaCal:Ely")
    with pytest.raises(vault.WrongPin):
        vault.load_login("visaCal:Ely", "0000")
    assert vault.load_login("visaCal:Ely", "4821")["password"] == SECRET
    with pytest.raises(vault.WrongPin):
        vault.save_login("max", "Ely", {"username": "u", "password": "p"}, "0000")
    vault.set_pin("4821", "9999")
    assert vault.load_login("visaCal:Ely", "9999")["password"] == SECRET
    vault.set_pin("9999", "")
    assert not vault.has_pin() and vault.load_login("visaCal:Ely")["password"] == SECRET


def test_forget_one_and_forget_all():
    vault.save_login("max", "Ely", {"username": "u", "password": "p"})
    vault.save_login("amex", "Ely", {"id": "123456789", "card6Digits": "375512", "password": "p"})
    assert vault.forget("max:Ely") and [e["id"] for e in vault.list_logins()] == ["amex:Ely"]
    vault.forget_all()
    assert not vault.path().exists() and vault.list_logins() == []


def test_field_checks():
    assert cardfetch.check_fields("max", {"username": "a", "password": "b"}) is None
    assert "6 digits" in cardfetch.check_fields("isracard", {"id": "123456789", "card6Digits": "1234", "password": "x"})
    assert "digits only" in cardfetch.check_fields("amex", {"id": "12a", "card6Digits": "123456", "password": "x"})
    assert "Fill in" in cardfetch.check_fields("visaCal", {"username": "", "password": "x"})


# ---- running the scraper ------------------------------------------------------------------------------------------

def test_login_goes_to_the_scraper_on_stdin_only(monkeypatch, secrets_dir):
    seen = {}

    class R:
        stdout = "noise\n" + cardfetch.MARK + "\n" + json.dumps({"ok": True, "accounts": []}) + "\n"
        stderr = ""

    def fake_run(args, **kw):
        seen.update(args=args, env=kw.get("env"), input=kw.get("input"))
        return R()
    monkeypatch.setattr(cardfetch, "find_node", lambda: "node")
    monkeypatch.setattr(cardfetch.subprocess, "run", fake_run)
    secrets_dir.joinpath("scraper").mkdir(parents=True)
    res = cardfetch.run_scraper("max", {"username": "ely1", "password": SECRET}, date(2026, 7, 1))
    assert res == {"ok": True, "accounts": []}
    assert SECRET not in " ".join(seen["args"]) and all(SECRET not in str(v) for v in seen["env"].values())
    assert json.loads(seen["input"])["credentials"]["password"] == SECRET


def test_errors_become_plain_reasons():
    assert "wrong" in cardfetch.explain({"errorType": "INVALID_PASSWORD"})
    assert "SMS" in cardfetch.explain({"errorType": "TWO_FACTOR_RETRIEVER_MISSING"})
    assert "Import a file" in cardfetch.explain({"errorType": "GENERIC"})


def test_fetch_file_holds_transactions_but_no_login(home):
    res = fetched([site_txn("2026-09-03", "SUPER", -100.0, "1")])
    p = cardfetch.write_fetch_file(home.inbox, "max", "Ely", res, datetime(2026, 10, 5, 9, 0))
    body = p.read_text(encoding="utf-8")
    assert p.suffix == ".json" and SECRET not in body and "password" not in body
    assert cardfetch.write_fetch_file(home.inbox, "max", "Ely", fetched([site_txn("2026-09-03", "X", -1.0, "2", status="pending")]), datetime.now()) is None


# ---- reading and importing ----------------------------------------------------------------------------------------

def _drop(home, company, res, when=datetime(2026, 10, 5, 9, 0)):
    return cardfetch.write_fetch_file(home.inbox, company, "Ely", res, when)


def test_reader_groups_by_billing_date_and_skips_pending():
    d = {"format": reader.FORMAT, "company": "isracard", "accounts": [{"accountNumber": "4580-1111", "txns": [
        site_txn("2026-09-03", "SUPER", -100.0, "1"),
        site_txn("2026-09-28", "LATER", -40.0, "2", billed="2026-11-02"),
        site_txn("2026-10-04", "WAITING", -9.0, None, status="pending"),
        {**site_txn("2026-08-10", "FRIDGE", -300.0, "3"), "originalAmount": -1800.0, "type": "installments",
         "installments": {"number": 2, "total": 6}},
        {**site_txn("2026-09-12", "AMAZON", -37.4, "4"), "originalAmount": -10.0, "originalCurrency": "USD"},
    ]}]}
    pf = reader.parse(d)
    assert pf.file_kind == "card_fetch" and [s.billing_date.isoformat() for s in pf.statements] == ["2026-10-02", "2026-11-02"]
    oct_ = pf.statements[0]
    assert oct_.last4 == "1111" and len(oct_.txns) == 3
    fridge = next(t for t in oct_.txns if t.description == "FRIDGE")
    assert (fridge.amount, fridge.instalment_no, fridge.instalment_total, fridge.instalment_full) == (-300.0, 2, 6, 1800.0)
    amazon = next(t for t in oct_.txns if t.description == "AMAZON")
    assert (amazon.amount, amazon.currency, amazon.orig_amount, amazon.orig_currency) == (-37.4, "ILS", -10.0, "USD")


def test_fetch_imports_and_a_second_fetch_adds_nothing_twice(home, conn):
    _drop(home, "max", fetched([site_txn("2026-09-03", "SUPER", -100.0, "1"), site_txn("2026-09-05", "CAFE", -50.0, "2")]))
    rep = importer.import_inbox(home, conn, date(2026, 10, 5))
    assert [f.status for f in rep.files] == ["imported"] and rep.files[0].new_txns == 2
    acct = conn.execute("SELECT id, label FROM accounts WHERE issuer = 'max'").fetchone()
    assert acct["label"] == "Max 1234"
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE source = 'fetch'").fetchone()[0] == 2
    _drop(home, "max", fetched([site_txn("2026-09-03", "SUPER", -100.0, "1"), site_txn("2026-09-05", "CAFE", -50.0, "2"),
                                site_txn("2026-09-07", "NEW", -20.0, "3")]), datetime(2026, 10, 9, 9, 0))
    rep = importer.import_inbox(home, conn, date(2026, 10, 9))
    assert (rep.files[0].new_txns, rep.files[0].skipped_txns) == (1, 2)


def test_file_after_fetch_and_fetch_after_file_are_not_counted_twice(home, conn, monkeypatch):
    _drop(home, "isracard", fetched([site_txn("2026-09-03", "SUPER EXAMPLE", -100.0, "999"),
                                     site_txn("2026-09-05", "CAFE EXAMPLE", -50.0, "998")], number="1111"))
    importer.import_inbox(home, conn, date(2026, 10, 5))
    rep = run_import(home, conn, monkeypatch, [("statement.xlsx", card_file(last4="1111"))])    # same two purchases, other vouchers
    assert (rep.files[0].new_txns, rep.files[0].skipped_txns) == (0, 2)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    assert not conn.execute("SELECT 1 FROM discrepancies WHERE type = 'statement_total_mismatch' AND status = 'open'").fetchone()

    rep = run_import(home, conn, monkeypatch, [("statement2.xlsx", card_file(last4="2222", txns=[
        card_txn(date(2026, 9, 10), "BOOKS", 80.0, "b1")]))])
    assert rep.files[0].new_txns == 1
    monkeypatch.undo()
    _drop(home, "isracard", fetched([site_txn("2026-09-10", "BOOKS", -80.0, "77"), site_txn("2026-09-11", "EXTRA", -5.0, "78")],
                                    number="2222"), datetime(2026, 10, 6, 9, 0))
    rep = importer.import_inbox(home, conn, date(2026, 10, 6))
    assert (rep.files[0].new_txns, rep.files[0].skipped_txns) == (1, 1)


def test_a_card_typed_in_by_hand_with_its_last_4_takes_the_fetched_lines(home, conn):
    accounts.add_account(conn, "card", "manual", "בהצדעה member card", last4="1234")
    conn.commit()
    _drop(home, "max", fetched([site_txn("2026-09-03", "SUPER", -100.0, "1")]))
    importer.import_inbox(home, conn, date(2026, 10, 5))
    rows = conn.execute("SELECT label, issuer FROM accounts WHERE kind = 'card'").fetchall()
    assert [(r["label"], r["issuer"]) for r in rows] == [("בהצדעה member card", "max")]


def test_a_stray_json_is_set_aside(home, conn):
    (home.inbox / "notes.json").write_text('{"hello": 1}', encoding="utf-8")
    rep = importer.import_inbox(home, conn, date(2026, 10, 5))
    assert rep.files[0].status == "unrecognised"


# ---- the pages ------------------------------------------------------------------------------------------------------

@pytest.fixture
def app(home, conn):
    accounts.add_owner(conn, "Ely")
    accounts.add_owner(conn, "Shir")
    conn.commit()
    return App(home, today="2026-10-05")


def test_cards_pages_are_blocked_from_other_devices(home, conn):
    accounts.add_owner(conn, "Ely")
    conn.commit()
    lan = App(home, pin="123456", lan=True, today="2026-10-05")
    res = lan.handle("GET", "/cards", {"host": "192.168.1.5:8765", "x-client-ip": "192.168.1.20",
                                       "cookie": f"auth={lan._auth_token()}"})
    assert res.status == 403 and "Only on the computer" in text(res)
    srv = App(home, server=True, today="2026-10-05")
    assert srv.handle("GET", "/cards", {"host": "localhost:8765"}).status in (401, 403)          # signed in or not, never shown


def test_save_login_from_the_page_never_shows_it_again(app):
    assert get(app, "/cards").status == 200
    res = post(app, "/cards/save", {"company": "isracard", "person": "Ely", "f_isracard_id": "123456789",
                                    "f_isracard_card6Digits": "458011", "f_isracard_password": SECRET})
    assert res.status == 303 and "saved" in dict(res.headers)["Set-Cookie"]
    page = text(get(app, "/cards"))
    assert "Isracard" in page and SECRET not in page and "458011" not in page and "123456789" not in page
    assert vault.load_login("isracard:Ely")["card6Digits"] == "458011"
    bad = post(app, "/cards/save", {"company": "isracard", "person": "Ely", "f_isracard_id": "1", "f_isracard_card6Digits": "45",
                                    "f_isracard_password": "x"})
    assert "err" in dict(bad.headers)["Set-Cookie"]
    no_csrf = app.handle("POST", "/cards/save", {"host": "localhost:8765"}, b"company=max&person=Ely")
    assert no_csrf.status == 403


def test_fetch_from_the_page_brings_lines_into_review(app, conn, monkeypatch):
    vault.save_login("max", "Ely", {"username": "u", "password": SECRET})
    got = {}

    def fake(company, creds, start, show_browser=False):
        got.update(company=company, creds=creds, start=start)
        return fetched([site_txn("2026-09-03", "SUPER", -100.0, "1")])
    monkeypatch.setattr(cardfetch, "run_scraper", fake)
    monkeypatch.setattr(cardfetch, "setup_status", lambda: {"ready": True, "windows": True, "vault": True, "node_ok": True, "npm": True,
                                                             "node_version": "24.1.0", "browser": "chrome", "installed": "6.13.0",
                                                             "pinned": "6.13.0", "update_waiting": False})
    assert "Fetch cards" in text(get(app, "/"))
    res = post(app, "/cards/fetch", {})
    assert res.status == 303
    for _ in range(100):
        if not cardfetch.JOB.snapshot()["running"]:
            break
        time.sleep(0.05)
    job = cardfetch.JOB.snapshot()
    assert "1 new line" in job["summary"] and job["results"][0]["ok"]
    assert got["company"] == "max" and got["creds"]["password"] == SECRET and got["start"] == date(2026, 10, 5).fromordinal(date(2026, 10, 5).toordinal() - 120)
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE source = 'fetch'").fetchone()[0] == 1
    assert cardfetch.last_status()["max:Ely"]["ok"]
    assert app.active == 0
    page = text(get(app, "/cards"))
    assert "Last fetch" in page and SECRET not in page


def test_fetch_with_a_wrong_pin_is_refused_at_once(app, monkeypatch):
    vault.save_login("max", "Ely", {"username": "u", "password": "p"})
    vault.set_pin(None, "4821")
    monkeypatch.setattr(cardfetch, "setup_status", lambda: {"ready": True})
    res = post(app, "/cards/fetch", {"pin": "0000"})
    assert "Wrong" in dict(res.headers)["Set-Cookie"] and not cardfetch.JOB.snapshot()["running"]


def test_forget_all_from_the_page(app):
    vault.save_login("max", "Ely", {"username": "u", "password": "p"})
    assert "err" in dict(post(app, "/cards/forget-all", {}).headers)["Set-Cookie"]
    post(app, "/cards/forget-all", {"sure": "1"})
    assert vault.list_logins() == []


def test_kit_is_pinned_with_hashes_and_no_install_scripts():
    pkg = json.loads(cardfetch._kit("package.json"))
    lock = json.loads(cardfetch._kit("package-lock.json"))
    assert pkg["dependencies"]["israeli-bank-scrapers"] == cardfetch.pinned_version() == "6.13.0"
    entries = [v for k, v in lock["packages"].items() if k]
    assert entries and all(v.get("integrity", "").startswith("sha512-") for v in entries)
    assert all(v.get("resolved", "").startswith("https://registry.npmjs.org/") for v in entries)


# ---- pending charges ------------------------------------------------------------------------------------------------

def test_pending_charges_are_shown_apart_and_replaced_by_the_next_fetch(home, conn):
    from ftracker import pending, summary
    res = fetched([site_txn("2026-10-03", "SUPER", -100.0, "1", billed="2026-11-02"),
                   site_txn("2026-10-04", "CAFE WAITING", -42.0, None, status="pending"),
                   {**site_txn("2026-10-04", "SHOP ABROAD", -9.0, None, status="pending"), "chargedCurrency": "USD"}])
    _drop(home, "max", res)
    importer.import_inbox(home, conn, date(2026, 10, 5))
    assert pending.replace(conn, "max:Ely", "max", res["accounts"], "2026-10-05T09:00:00") == 2
    rows = pending.current(conn, "2026-10-05")
    assert {r["description"] for r in rows} == {"CAFE WAITING", "SHOP ABROAD"} and pending.total(rows) == 42.0
    assert rows[0]["account"] == "Max 1234"
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1          # pending never becomes a counted line
    # next fetch: the café is final now, the shop is still pending
    later = fetched([site_txn("2026-10-04", "CAFE WAITING", -42.0, "2", billed="2026-11-02"),
                     {**site_txn("2026-10-04", "SHOP ABROAD", -9.0, None, status="pending"), "chargedCurrency": "USD"}])
    assert pending.replace(conn, "max:Ely", "max", later["accounts"], "2026-10-07T09:00:00") == 1
    assert [r["description"] for r in pending.current(conn, "2026-10-07")] == ["SHOP ABROAD"]
    assert pending.current(conn, "2026-10-30") == []                                       # not seen for two weeks: dropped


def test_a_fetch_shows_pending_on_overview_and_transactions(app, conn, monkeypatch):
    vault.save_login("max", "Ely", {"username": "u", "password": "p"})
    monkeypatch.setattr(cardfetch, "run_scraper", lambda *a, **k: fetched([
        site_txn("2026-09-03", "SUPER", -100.0, "1"), site_txn("2026-10-04", "CAFE WAITING", -42.0, None, status="pending")]))
    monkeypatch.setattr(cardfetch, "setup_status", lambda: {"ready": True})
    post(app, "/cards/fetch", {})
    for _ in range(100):
        if not cardfetch.JOB.snapshot()["running"]:
            break
        time.sleep(0.05)
    job = cardfetch.JOB.snapshot()
    assert "1 pending" in job["summary"] and "1 line(s) read" in job["results"][0]["message"]
    ov = text(get(app, "/"))
    assert "Pending on cards" in ov and "CAFE WAITING" in ov
    tx = text(get(app, "/transactions"))
    assert 'id="pending"' in tx and "not counted" in tx


def test_failure_shows_the_real_reason_but_never_the_login():
    res = cardfetch.scrub({"ok": False, "errorType": "GENERIC", "errorMessage": f"login failed for 458011 at form\n step 3"},
                          {"id": "123456789", "card6Digits": "458011", "password": SECRET})
    msg = cardfetch.explain(res)
    assert "Details: GENERIC: login failed for ••• at form step 3" in msg and "458011" not in msg
    assert "Details" not in cardfetch.explain({"errorType": "INVALID_PASSWORD", "errorMessage": "x"})
