from datetime import date
from urllib.parse import urlencode

import pytest

from ftracker import accounts, balances, fx, goals, sheet_import
from ftracker.web import App
from .helpers import bank_file, card_file, card_txn, run_import
from .test_sheet_import import _workbook

H = {"host": "localhost:8765"}


@pytest.fixture
def app(home, conn, monkeypatch, tmp_path):
    accounts.add_owner(conn, "Ely"); accounts.add_owner(conn, "Shir")
    sheet_import.save(conn, sheet_import.parse_workbook(_workbook(tmp_path / "h.xlsx")))
    run_import(home, conn, monkeypatch, [
        ("c.xlsx", card_file(txns=[card_txn(date(2026, 9, 3), "SUPER <script>alert(1)</script>", 100.0, "q1"),
                                   card_txn(date(2026, 9, 9), "ONE OFF SERVICE", 3500.0, "q2")])),
        ("b.pdf", bank_file([(date(2026, 9, 2), "ל.מאסטרקרד", -3600.0), (date(2026, 9, 5), "עמל.ערוץ יש 10", -16.5)])),
    ])
    fx.set_rate(conn, "2025-09-17", "USD", "ILS", 3.5, "manual")
    conn.commit()                       # the app opens its own connections; do not hold a write lock
    return App(home, today="2026-10-05")


def get(app, path, **kw):
    return app.handle("GET", path, {**H, **kw.get("headers", {})})


def post(app, path, data, headers=None):
    body = urlencode({"csrf": app.csrf, **data}, doseq=True).encode()
    return app.handle("POST", path, {**H, **(headers or {})}, body)


def text(res):
    return res.body.decode("utf-8")


@pytest.mark.parametrize("path", ["/", "/review", "/items", "/items?status=all", "/imports", "/balances", "/goals", "/?cur=USD&owner=Ely",
                                  "/networth", "/networth?cur=USD&owner=Ely", "/trends", "/trends?range=6&owner=Shir", "/trends?cat=Groceries"])
def test_every_page_renders(app, path):
    res = get(app, path)
    assert res.status == 200 and "<title>" in text(res)


def test_spending_and_wealth_show_the_numbers(app):
    t = text(get(app, "/spending"))
    assert "Income and spending" in t and "Total spending by month" in t and "Every category, every month" in t
    assert "28,000" in t                                          # March 2025 income from the old sheet
    assert 'data-tip="' in t and "Table view" in t                # tooltips and the table alternative
    w = text(get(app, "/wealth"))
    assert "Net worth" in w and "Change between updates" in w and "What it is made of" in w


def test_home_walks_through_the_steps_and_upload_ticks_boxes(app):
    h = text(get(app, "/"))
    for step in ("Upload your statements", "Confirm categories", "Check for problems", "Update balances", "Submit"):
        assert step in h
    u = text(get(app, "/upload"))
    assert "Card statements" in u and "Bank statements" in u and "Numbers you type" in u
    assert "Isracard" in u and "Received" in u                    # the statement imported in the fixture is ticked
    assert "Shir&#x27;s Isracard" in u or "Shir's Isracard" in u   # a source with no file yet is listed as needed


def test_statement_text_is_escaped(app):
    t = text(get(app, "/review"))
    assert "<script>alert(1)</script>" not in t and "&lt;script&gt;" in t


def test_only_this_computer_may_open_it_unless_lan_mode(app, home):
    assert app.handle("GET", "/", {"host": "evil.example"}).status == 421
    assert App(home, lan=True, pin="1", today="2026-10-05").handle("GET", "/", {"host": "192.168.1.5:8765"}).status == 401


def test_posts_need_the_page_token_and_the_same_origin(app):
    assert app.handle("POST", "/who", H, b"who=Ely").status == 403
    assert post(app, "/who", {"who": "Shir"}, {"origin": "http://evil.example"}).status == 403
    assert post(app, "/who", {"who": "Shir"}, {"origin": "http://localhost:8765"}).status == 303


def test_pin_gate(home):
    a = App(home, lan=True, pin="1234", today="2026-10-05")
    h = {"host": "192.168.1.5:8765"}
    assert a.handle("POST", "/login", h, b"pin=0000").status == 403
    ok = a.handle("POST", "/login", h, b"pin=1234")
    assert ok.status == 303
    cookie = dict(ok.headers)["Set-Cookie"].split(";")[0]
    assert a.handle("GET", "/", {**h, "cookie": cookie}).status == 200


def test_approve_a_merchant_and_remember_it(app, home):
    from ftracker.db import connect
    res = post(app, "/approve", {"key": "ONE OFF SERVICE", "category": "Home", "learn": "1"})
    assert res.status == 303
    conn = connect(home.db_path)
    row = conn.execute("SELECT c.name, t.category_status FROM transactions t JOIN categories c ON c.id = t.category_id "
                       "WHERE t.description = 'ONE OFF SERVICE'").fetchone()
    assert (row[0], row[1]) == ("Home", "approved")
    assert conn.execute("SELECT 1 FROM rules WHERE source = 'learned' AND pattern = 'ONE OFF SERVICE'").fetchone()


def test_comment_on_an_item_with_status_and_follow_up(app, home):
    from ftracker.db import connect
    conn = connect(home.db_path)
    item = conn.execute("SELECT id FROM discrepancies WHERE type = 'large_item'").fetchone()[0]
    post(app, "/who", {"who": "Shir"})
    res = post(app, f"/item/{item}/comment", {"body": "yearly <b>service</b>", "status": "explained", "follow_up": "2026-12-01"},
               {"cookie": "who=Shir"})
    assert res.status == 303
    t = text(get(app, f"/item/{item}", headers={"cookie": "who=Shir"}))
    assert "yearly &lt;b&gt;service&lt;/b&gt;" in t and "Shir" in t and "follow up 2026-12-01" in t


def test_submit_needs_acknowledgement_then_records_the_time_and_reopen_needs_a_reason(app, home):
    from ftracker.db import connect
    conn = connect(home.db_path)
    batch = conn.execute("SELECT id FROM batches ORDER BY id DESC LIMIT 1").fetchone()[0]
    r = post(app, f"/imports/{batch}/submit", {})
    assert "Tick the box" in _flash(r)
    assert post(app, f"/imports/{batch}/submit", {"ack": "1"}).status == 303
    assert conn.execute("SELECT status FROM batches WHERE id = ?", (batch,)).fetchone()[0] == "committed"
    assert "Submitted" in text(get(app, "/imports"))
    assert "Why are you reopening" in text(get(app, "/imports"))
    assert "err|" in _flash_raw(post(app, f"/imports/{batch}/reopen", {"reason": "  "}))
    assert post(app, f"/imports/{batch}/reopen", {"reason": "fix"}).status == 303


def _flash_raw(res):
    from urllib.parse import unquote
    return unquote(next(v for k, v in res.headers if k == "Set-Cookie" and v.startswith("flash=")).split(";")[0][6:])


def _flash(res):
    return _flash_raw(res).split("|", 1)[1]


def test_balances_form_saves_filled_rows_only_and_flash_shows_once(app, home):
    from ftracker.db import connect
    conn = connect(home.db_path)
    acct = conn.execute("SELECT id FROM accounts WHERE label = 'ETRADE'").fetchone()[0]
    other = conn.execute("SELECT id FROM accounts WHERE label = 'שיר פנסיה כלל'").fetchone()[0]
    res = post(app, "/balances", {"as_of": "2026-10-01", f"bal_{acct}": "140,000", f"bal_{other}": ""})
    assert "1 balance(s) saved" in _flash(res)
    assert conn.execute("SELECT amount FROM balances WHERE account_id = ? AND as_of = '2026-10-01'", (acct,)).fetchone()[0] == 140000
    page = get(app, "/balances", headers={"cookie": "flash=" + "ok%7Chello"})
    assert "hello" in text(page) and any("flash=; Max-Age=0" in v for k, v in page.headers)
    assert "hello" not in text(get(app, "/balances"))


def test_add_account_and_goal_and_birth_date(app, home):
    from ftracker.db import connect
    assert post(app, "/accounts", {"label": "Rainy day", "kind": "savings", "currency": "ILS", "owner": "Shir"}).status == 303
    assert post(app, "/birth", {"owner": "Ely", "birth": "1990-06-15"}).status == 303
    conn = connect(home.db_path)
    aid = conn.execute("SELECT id FROM accounts WHERE label = 'Rainy day'").fetchone()[0]
    res = post(app, "/goals", {"name": "Safety net", "amount": "50,000", "currency": "ILS", "age": "40", "owner": "Ely",
                               "account": [str(aid)], "return": "3"})
    assert res.status == 303
    g = conn.execute("SELECT * FROM goals").fetchone()
    assert (g["target_age"], g["annual_return"]) == (40, 0.03)
    assert "Safety net" in text(get(app, "/goals"))
    bad = post(app, "/goals", {"name": "x", "amount": "5", "age": "40", "owner": "Shir"})
    assert "err|" in _flash_raw(bad)                            # Shir has no birth date: a clear error, not a crash


def test_import_button_reports_empty_and_recognised(app, home, monkeypatch):
    monkeypatch.undo()                  # use the real parsers for this one
    assert "inbox is empty" in _flash(post(app, "/import", {}))
    (home.inbox / "junk.xlsx").write_bytes(b"nope")
    assert "not recognised" in _flash(post(app, "/import", {}))


def test_upload_page_lists_cal_card_expandable_balances_and_sheet_link(app):
    u = text(get(app, "/upload"))
    assert "Cal 5962" in u
    assert "Balances (monthly)" in u and "show which" in u
    assert "Google Sheet (optional)" in u and 'name="back" value="/upload"' in u


def test_wealth_filter_leaves_chosen_accounts_out_everywhere(app, conn):
    before = balances.net_worth(conn, "2026-10-05")["total"]
    ids = [r["account_id"] for r in balances.latest(conn, "2026-10-05")]
    assert ids
    page = text(get(app, "/wealth"))
    assert "Choose what counts in net worth" in page and "/wealth/include" in page
    keep = ids[1:]
    post(app, "/wealth/include", {"shown": [str(i) for i in ids], **{f"inc_{i}": "1" for i in keep}})
    after = balances.net_worth(conn, "2026-10-05")["total"]
    assert after < before or before == 0
    assert "Not counting 1 account(s)" in text(get(app, "/wealth"))
    assert balances.net_worth(conn, "2026-10-05", everything=True)["total"] == before


def test_spending_includes_bank_statement_lines(app):
    t = text(get(app, "/spending"))
    assert "Where it was recorded" in t and "bank statements" in t


def test_category_drill_down_shows_merchants_and_every_payment(app):
    t = text(get(app, "/spending?cat=Food"))
    assert "What makes up Food" in t and "Every payment" in t and "Biggest merchants" in t
    month = text(get(app, "/spending?cat=Food&month=2025-03"))
    assert "2025-03 only" in month


def test_settings_and_upload_pages_offer_the_watched_folder(app, tmp_path):
    assert "Statements from a folder" in text(get(app, "/setup"))
    assert "Google Drive folder" in text(get(app, "/upload")) or "picked up automatically" in text(get(app, "/upload"))
    d = tmp_path / "gd"; d.mkdir()
    res = post(app, "/watch/set", {"folder": str(d)})
    assert res.status == 303
    assert "picking up statements from" in text(get(app, "/upload")) and str(d) in text(get(app, "/upload"))
    bad = post(app, "/watch/set", {"folder": str(tmp_path / "nope")})
    assert bad.status == 303


def test_card_paid_from_an_unimported_account_is_not_flagged(app, conn):
    from ftracker import accounts as acc, reconcile
    card = conn.execute("SELECT id FROM accounts WHERE kind = 'card' AND issuer != 'sheet' LIMIT 1").fetchone()[0]
    assert "Which account pays each card" in text(get(app, "/setup"))
    conn.execute("DELETE FROM transactions WHERE kind = 'card_payment'")           # no bank payment matches this card
    conn.commit()
    reconcile.match_card_payments(conn, 1); conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM discrepancies WHERE type = 'no_bank_payment' AND status = 'open'").fetchone()[0] >= 1
    res = post(app, "/setup/card-payer", {"card": str(card), "payer": "external"})
    assert res.status == 303
    reconcile.match_card_payments(conn, 1); conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM discrepancies WHERE type = 'no_bank_payment' AND status = 'open'").fetchone()[0] == 0
    acc.set_card_payer(conn, card, "")
    assert conn.execute("SELECT pays_externally FROM accounts WHERE id = ?", (card,)).fetchone()[0] == 0
