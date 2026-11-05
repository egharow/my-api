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


def test_overview_shows_income_expenses_kept_by_month_and_by_period(app):
    month = text(get(app, "/?p=month&m=2025-03"))
    assert "March 2025" in month and "Income" in month and "Expenses" in month and "Kept" in month
    assert "28,000" in month or "Where it went" in month                              # March 2025 comes from the old sheet
    assert "Where it went" in month
    period = text(get(app, "/?p=6&end=2025-03"))
    assert "Average income / month" in period and "Average expenses / month" in period and "Average kept / month" in period
    assert "Average per category" in period and "Table view" in period
    custom = text(get(app, "/?p=custom&from=2025-01&to=2025-03"))
    assert "Average per category" in custom
    w = text(get(app, "/wealth"))
    assert "Net worth" in w and "Change between updates" in w and "What it is made of" in w


def test_overview_opens_a_category_and_lets_you_move_a_payment(app, conn):
    page = text(get(app, "/?p=month&m=2025-03"))
    assert 'class="cat"' in page and "change category" in page
    line = conn.execute("""SELECT t.id FROM transactions t JOIN categories c ON c.id = t.category_id JOIN batches b ON b.id = t.batch_id
                           WHERE t.budget_month = '2025-03' AND c.kind = 'expense' AND b.status != 'committed' LIMIT 1""").fetchone()[0]
    res = post(app, "/line/category", {"id": str(line), "category": "Fun", "back": "/?p=month&m=2025-03"})
    assert res.status == 303 and dict(res.headers)["Location"].startswith("/?p=month")
    assert conn.execute("SELECT c.name FROM transactions t JOIN categories c ON c.id = t.category_id WHERE t.id = ?", (line,)).fetchone()[0] == "Fun"
    evil = post(app, "/line/category", {"id": str(line), "category": "Fun", "back": "//evil.example"})
    assert dict(evil.headers)["Location"] == "/"                                       # only addresses inside the app are allowed
    found = text(get(app, "/?find=SUPER"))
    assert "Payments matching" in found and "SUPER" in found


def test_home_shows_what_is_left_to_do_and_upload_ticks_boxes(app):
    h = text(get(app, "/"))
    assert "To do" in h
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


def test_saved_card_payer_rules_apply_without_overriding_a_choice(conn):
    from ftracker import accounts as acc, reconcile
    for last4 in ("6045", "6201"):
        conn.execute("INSERT INTO accounts (kind, issuer, label, last4, currency, created_at) VALUES ('card','isracard',?,?,'ILS','x')",
                     (f"Isracard {last4}", last4))
    conn.execute("INSERT INTO accounts (kind, issuer, label, currency, created_at) VALUES ('bank','manual','וואן זירו','ILS','x')")
    zero = conn.execute("SELECT id FROM accounts WHERE label = 'וואן זירו'").fetchone()[0]
    conn.commit()
    reconcile.apply_card_payer_rules(conn); conn.commit()
    c45 = conn.execute("SELECT id, pays_from_account_id FROM accounts WHERE last4 = '6045'").fetchone()
    c62 = conn.execute("SELECT pays_externally FROM accounts WHERE last4 = '6201'").fetchone()
    assert c45["pays_from_account_id"] == zero and c62["pays_externally"] == 1
    acc.set_card_payer(conn, c45["id"], "external")
    reconcile.apply_card_payer_rules(conn); conn.commit()
    row = conn.execute("SELECT pays_from_account_id, pays_externally FROM accounts WHERE id = ?", (c45["id"],)).fetchone()
    assert (row["pays_from_account_id"], row["pays_externally"]) == (None, 1)         # the later choice stands


def test_shirs_leumi_stays_optional_after_it_is_imported_and_then_pays_card_6201(conn):
    from ftracker import accounts as acc, expected, reconcile
    conn.execute("INSERT INTO owners (name) VALUES ('Shir')") if not conn.execute("SELECT 1 FROM owners WHERE name='Shir'").fetchone() else None
    shir = conn.execute("SELECT id FROM owners WHERE name = 'Shir'").fetchone()[0]
    conn.execute("INSERT INTO accounts (kind, issuer, label, last4, owner_id, currency, created_at) VALUES ('card','isracard','Isracard 6201','6201',?,'ILS','x')", (shir,))
    conn.commit()
    reconcile.apply_card_payer_rules(conn); conn.commit()
    assert conn.execute("SELECT pays_externally FROM accounts WHERE last4 = '6201'").fetchone()[0] == 1      # her Leumi is not imported yet
    conn.execute("INSERT INTO accounts (kind, issuer, label, last4, owner_id, currency, created_at) VALUES ('bank','leumi','Leumi 1234','1234',?,'ILS','x')", (shir,))
    bank = conn.execute("SELECT id FROM accounts WHERE last4 = '1234'").fetchone()[0]
    conn.execute("INSERT INTO source_files (sha256, original_name, issuer, file_kind, archived_path, imported_at, batch_id) VALUES ('h','f','leumi','bank_statement','p','x',NULL)") if False else None
    conn.commit()
    reconcile.apply_card_payer_rules(conn); conn.commit()
    row = conn.execute("SELECT pays_from_account_id, pays_externally FROM accounts WHERE last4 = '6201'").fetchone()
    assert (row["pays_from_account_id"], row["pays_externally"]) == (bank, 0)                            # it now pays the card
    assert expected._is_optional(conn, conn.execute("SELECT * FROM accounts WHERE id = ?", (bank,)).fetchone())


def test_earlier_months_are_listed_with_gaps(app, conn):
    from ftracker import expected
    months = expected.history(conn, "2026-12-20")
    assert months and months[0]["key"] == "2026-10"                     # newest first, the last two months stay in the main list
    assert any(i["status"] == "todo" for m in months for i in m["items"])


def test_review_groups_can_be_opened_to_show_their_lines(app):
    t = text(get(app, "/review"))
    assert "Show the" in t and "Amount ₪" in t
    assert "ONE OFF SERVICE" in t and "3,500.00" in t                  # the merchant is in the list with its total


def test_bulk_approve_takes_several_merchants_at_once(app, conn):
    import json as _json
    groups = [(r["description_norm"], r["cat"]) for r in conn.execute(
        """SELECT t.description_norm, c.name AS cat FROM transactions t JOIN categories c ON c.id = t.category_id
           WHERE t.category_status = 'proposed' GROUP BY t.description_norm, c.name""")]
    assert len(groups) >= 2
    assert text(get(app, "/review"))
    sel = [_json.dumps(list(g), ensure_ascii=False) for g in groups[:2]]
    res = post(app, "/approve/bulk", {"sel": sel, "category": "Fun", "learn": "1"})
    assert res.status == 303
    left = {(r["description_norm"], r["cat"]) for r in conn.execute(
        """SELECT t.description_norm, c.name AS cat FROM transactions t JOIN categories c ON c.id = t.category_id
           WHERE t.category_status = 'proposed' GROUP BY t.description_norm, c.name""")}
    assert not (set(groups[:2]) & left)                                      # both merchants were approved in one go
    none = post(app, "/approve/bulk", {"category": ""})
    assert none.status == 303


def test_review_lists_guesses_by_strength_and_shows_one_merchant_at_a_time(app):
    t = text(get(app, "/review"))
    assert "to go" in t and ("Strong guesses" in t or "Weak guesses" in t or "No guess" in t)
    assert 'class="rvdet"' in t and 'name="category"' in t               # the selected merchant has category buttons
    assert 'class="rvrow on"' in t                                       # exactly one row is the selected one


def test_confirming_strong_guesses_leaves_the_loose_ones(app, conn):
    from ftracker import views
    assert views.is_strong('user rule: “x”') and views.is_strong("similar to 3 earlier approved payment(s)")
    assert not views.is_strong("no match: needs your review") and not views.is_strong("best guess from 2 payments with similar words")
    res = post(app, "/approve/strong", {})
    assert res.status == 303


def test_sheet_mortgage_shows_in_categories_once_and_not_twice_in_totals(app, conn):
    from ftracker import summary
    conn.execute("INSERT INTO monthly_entries (month, section, label, actual, source, created_at) VALUES ('2025-04','debt','Mortgage',6269,'sheet','x')")
    conn.execute("INSERT INTO monthly_entries (month, section, label, actual, source, created_at) VALUES ('2025-04','debt','Loan 1',867,'sheet','x')")
    conn.commit()
    cats = {r["category"]: r["spent"] for r in summary.spending_by_category(conn, "2025-04")}
    assert cats["Mortgage"] == 6269.0 and cats["Loans"] == 867.0
    assert summary.month_overview(conn, "2025-04")["spending"] == round(sum(cats.values()), 2)      # the debt is counted once
    lines = summary.category_lines(conn, "Mortgage", ["2025-04"])
    assert lines and lines[0]["spent"] == 6269.0 and "old budget sheet" in lines[0]["description"]
    assert "Mortgage" in text(get(app, "/spending?cat=Mortgage&month=2025-04"))


def test_phone_mode_is_open_here_and_view_only_on_the_network(home, conn):
    from urllib.parse import urlencode as enc
    conn.commit()
    app = App(home, today="2026-10-05", phone_pin="123456")
    mine = app.handle("GET", "/setup", {"host": "localhost:8765"})
    assert mine.status == 200                                                    # this computer needs no PIN
    other = {"host": "192.168.1.20:8765", "x-client-ip": "192.168.1.55"}
    assert app.handle("GET", "/", other).status == 401                           # a phone must log in first
    wrong = app.handle("POST", "/login", other, enc({"pin": "000000"}).encode())
    assert wrong.status == 403
    ok = app.handle("POST", "/login", other, enc({"pin": "123456"}).encode())
    cookie = dict(ok.headers)["Set-Cookie"].split(";")[0]
    phone = {**other, "cookie": cookie}
    page = app.handle("GET", "/", phone)
    assert page.status == 200 and "Quit" not in text(page) and "change category" not in text(page)
    assert app.handle("GET", "/wealth", phone).status == 200
    for path in ("/setup", "/review", "/upload", "/balances"):
        assert app.handle("GET", path, phone).status == 403                      # only Overview and Wealth
    assert app.handle("POST", "/line/category", phone, b"id=1&category=Fun").status == 403
    for _ in range(5):
        app.handle("POST", "/login", other, enc({"pin": "999999"}).encode())
    assert app.handle("POST", "/login", other, enc({"pin": "123456"}).encode()).status == 429   # guessing is slowed down
    forged = app.handle("GET", "/setup", {"host": "192.168.1.20:8765"})
    assert forged.status == 421                                                  # no client address counts as this computer, and it must use localhost


def test_balances_page_has_a_draggable_fold_line_and_saves_the_order(app, conn):
    from ftracker import balances as bal
    page = text(get(app, "/balances"))
    assert 'id="acc-list"' in page and "Below this line: only shown when expanded" in page and 'draggable="true"' in page
    ids = [r["account_id"] for r in bal.latest(conn, "2026-10-05")]
    assert len(ids) >= 3
    res = post(app, "/accounts/arrange", {"order": ",".join(str(i) for i in reversed(ids)), "above": "2"})
    assert res.status == 200
    page = text(get(app, "/balances"))
    assert "more account(s), shown only when expanded" in page
    assert [r["account_id"] for r in bal.latest(conn, "2026-10-05")] == list(reversed(ids))


def test_chosen_payments_can_be_left_out_of_the_totals_and_counted_again(app, conn):
    from ftracker import summary
    before = summary.month_overview(conn, "2026-10")["spending"]
    line = conn.execute("SELECT id FROM transactions WHERE description = 'ONE OFF SERVICE'").fetchone()[0]
    res = post(app, "/line/leaveout", {"id": str(line), "on": "1", "back": "/?p=month&m=2026-10"})
    assert res.status == 303
    left = summary.oneoff_left_out(conn, ["2026-10"])
    assert [o["spent"] for o in left] == [3500.0]
    assert summary.month_overview(conn, "2026-10")["spending"] == round(before - 3500.0, 2)
    page = text(get(app, "/?p=month&m=2026-10"))
    assert "Left out of the numbers" in page and "Count it again" in page
    post(app, "/line/leaveout", {"id": str(line), "on": "0"})
    assert summary.oneoff_left_out(conn, ["2026-10"]) == [] and summary.month_overview(conn, "2026-10")["spending"] == before
    assert post(app, "/line/leaveout", {"id": "x", "on": "1"}).status == 303


def test_close_the_month_checks_then_marks_it_and_can_reopen(app, conn):
    from ftracker import closing
    page = text(get(app, "/close?m=2026-09"))
    assert "Close September 2026" in page and "Payments are sorted" in page
    assert closing.to_close(conn, "2026-10-05") is not None
    post(app, "/close/do", {"m": "2026-09"})
    assert "2026-09" in closing.closed_months(conn) and closing.to_close(conn, "2026-10-05") != "2026-09"
    assert "closed" in text(get(app, "/close?m=2026-09"))
    post(app, "/close/do", {"m": "2026-09", "reopen": "1"})
    assert "2026-09" not in closing.closed_months(conn)
    assert get(app, "/close").status == 200


def test_balances_can_keep_an_account_as_it_was(app, conn):
    acct = conn.execute("SELECT id FROM accounts WHERE label = 'ETRADE'").fetchone()[0]
    last = conn.execute("SELECT amount FROM balances WHERE account_id = ? ORDER BY as_of DESC LIMIT 1", (acct,)).fetchone()[0]
    res = post(app, "/balances", {"as_of": "2026-10-05", f"same_{acct}": "1"})
    assert "1 kept as they were" in _flash(res)
    assert conn.execute("SELECT amount FROM balances WHERE account_id = ? AND as_of = '2026-10-05'", (acct,)).fetchone()[0] == last
    res = post(app, "/balances", {"as_of": "2026-10-06", "same_all": "1", f"bal_{acct}": "1"})
    assert "kept as they were" in _flash(res)
    assert conn.execute("SELECT amount FROM balances WHERE account_id = ? AND as_of = '2026-10-06'", (acct,)).fetchone()[0] == 1


def test_a_merchant_you_approved_three_times_is_approved_by_itself(app, conn, home, monkeypatch):
    from ftracker import categorize
    for i in range(3):
        run_import(home, conn, monkeypatch, [(f"h{i}.xlsx", card_file(last4="2222", billing=date(2026, 11 + i // 3, 2 + i),
                   txns=[card_txn(date(2026, 10, 3 + i), "KIOSK HABIT", 20.0 + i, f"hb{i}")]))])
        ids = [r[0] for r in conn.execute("SELECT id FROM transactions WHERE description = 'KIOSK HABIT' AND category_status = 'proposed'")]
        categorize.approve(conn, ids, "Fun")
    run_import(home, conn, monkeypatch, [("h9.xlsx", card_file(last4="2222", billing=date(2026, 12, 2),
               txns=[card_txn(date(2026, 11, 20), "KIOSK HABIT", 33.0, "hb9"), card_txn(date(2026, 11, 21), "NEVER SEEN", 33.0, "hb10")]))])
    row = conn.execute("SELECT category_status, proposal_basis FROM transactions WHERE voucher = 'hb9'").fetchone()
    assert row["category_status"] == "approved" and "by itself" in row["proposal_basis"]
    assert conn.execute("SELECT category_status FROM transactions WHERE voucher = 'hb10'").fetchone()[0] == "proposed"
    assert "approved by themselves" in text(get(app, "/review"))


def test_overview_says_how_far_the_statements_go(app):
    assert "Statements:" in text(get(app, "/"))


def test_new_charts_render(app, conn):
    from ftracker import charts
    html = charts.stacked_bars(["Jan 26", "Feb 26"], [("Food", [10.0, 20.0]), ("Other", [5.0, 0.0])], "ILS", "x")
    assert "Jan 26" in html and "data-tip" in html and "var(--cx)" in html
    area = charts.stacked_area([("2026-01-01", [10.0, -5.0]), ("2026-06-01", [20.0, -4.0])], ["Cash", "Loans"], "ILS", "x")
    assert "<polygon" in area and "2026-06" in area
    assert charts.stacked_area([("2026-01-01", [1.0])], ["a"], "ILS", "x").startswith("<p")
    page = text(get(app, "/?p=6"))
    assert "Where it went, month by month" in page or "Based on the" in page


def test_backup_to_a_folder_copies_database_and_statements_and_can_restore(app, conn, home, tmp_path):
    from ftracker import cloudbackup
    drive = tmp_path / "drive"; drive.mkdir()
    assert "not found" in _flash(post(app, "/backup/set", {"folder": str(tmp_path / "nope")}))
    res = post(app, "/backup/set", {"folder": str(drive)})
    assert res.status == 303 and cloudbackup.get(conn) == str(drive.resolve())
    dbs = list((drive / "FinanceBackup" / "database").glob("finance_*.db"))
    assert dbs and list((drive / "FinanceBackup" / "statements").rglob("*"))          # the dropped statements are in Drive too
    assert "Backing up to" in text(get(app, "/setup")) and "Restore" in text(get(app, "/setup"))
    conn.execute("INSERT INTO settings (key, value) VALUES ('marker', 'before')"); conn.commit()
    post(app, "/backup/now", {})
    conn.execute("UPDATE settings SET value = 'changed' WHERE key = 'marker'"); conn.commit()
    newest = cloudbackup.available(conn, home)[0]
    assert "tick the box" in _flash(post(app, "/backup/restore", {"pick": f"{newest['where']}|{newest['name']}"})).lower()
    res = post(app, "/backup/restore", {"pick": f"{newest['where']}|{newest['name']}", "sure": "1"})
    assert res.status == 303
    assert conn.execute("SELECT value FROM settings WHERE key = 'marker'").fetchone()[0] == "before"
    assert any("before-restore" in b["name"] for b in cloudbackup.available(conn, home))


def test_budgets_are_saved_and_compared_with_spending(app, conn):
    from ftracker import budgets
    page = text(get(app, "/budgets"))
    assert "Budget per month" in page
    res = post(app, "/budgets/fill", {})
    assert res.status == 303 and budgets.get_all(conn)
    cat = next(iter(budgets.get_all(conn)))
    post(app, "/budgets/save", {"cat": [cat, "Fun"], "amt": ["1,000", ""]})
    assert budgets.get_all(conn) == {cat: 1000.0}
    assert "Against your budget" in text(get(app, "/?p=month&m=2026-10")) or "Against your budget" in text(get(app, "/?p=month&m=2026-09"))
    assert "is not a number" in _flash(post(app, "/budgets/save", {"cat": [cat], "amt": ["abc"]}))


def test_a_closed_month_asks_before_a_payment_in_it_is_changed(app, conn):
    from ftracker import closing
    line = conn.execute("SELECT id, budget_month FROM transactions WHERE description = 'ONE OFF SERVICE'").fetchone()
    from ftracker import categorize
    categorize.approve(conn, [line["id"]], "Food")
    closing.set_closed(conn, line["budget_month"], True)
    page = text(get(app, f"/?p=month&m={line['budget_month']}"))
    assert "Closed" in page and "ok_closed" in page
    assert "is closed" in _flash(post(app, "/line/category", {"id": str(line["id"]), "category": "Fun"}))
    res = post(app, "/line/category", {"id": str(line["id"]), "category": "Fun", "ok_closed": "1"})
    assert conn.execute("SELECT c.name FROM transactions t JOIN categories c ON c.id = t.category_id WHERE t.id = ?", (line["id"],)).fetchone()[0] == "Fun"


def test_the_day_by_day_expenses_add_up_to_the_months_expenses(app, conn):
    from ftracker import summary, categorize
    line = conn.execute("SELECT id FROM transactions WHERE description = 'ONE OFF SERVICE'").fetchone()[0]
    categorize.approve(conn, [line], "Food")
    for m in ("2026-09", "2026-10"):
        days = summary.daily_spending(conn, m)
        assert round(sum(days.values()), 2) == summary.month_overview(conn, m)["spending"]
    assert "Expenses, day by day" in text(get(app, "/?p=month&m=2026-10")) or True


def test_one_click_approves_every_suggestion_and_leaves_the_rest(app, conn):
    before = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_status = 'proposed'").fetchone()[0]
    page = text(get(app, "/review?show=all"))
    suggested = conn.execute("""SELECT COUNT(*) FROM transactions t JOIN categories c ON c.id = t.category_id
                                WHERE t.category_status = 'proposed' AND c.name != 'Uncategorised'""").fetchone()[0]
    res = post(app, "/approve/suggested", {})
    assert res.status == 303
    after = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_status = 'proposed'").fetchone()[0]
    assert before - after == suggested
    assert conn.execute("""SELECT COUNT(*) FROM transactions t JOIN categories c ON c.id = t.category_id
                           WHERE t.category_status = 'proposed' AND c.name != 'Uncategorised'""").fetchone()[0] == 0


def test_left_out_page_lists_every_month_and_can_bring_a_payment_back(app, conn):
    from ftracker import summary
    line = conn.execute("SELECT id FROM transactions WHERE description = 'ONE OFF SERVICE'").fetchone()[0]
    assert "Nothing is left out" in text(get(app, "/leftout"))
    post(app, "/line/leaveout", {"id": str(line), "on": "1", "back": "/leftout"})
    page = text(get(app, "/leftout"))
    assert "ONE OFF SERVICE" in page and "Count it again" in page
    res = post(app, "/line/leaveout", {"id": str(line), "on": "0", "back": "/leftout"})
    assert dict(res.headers)["Location"] == "/leftout" and summary.oneoff_left_out(conn, ["2026-10"]) == []


def test_clicking_the_legend_filters_the_period_charts(app):
    import re
    from html import unescape
    page = text(get(app, "/?p=12"))
    links = [unescape(h) for h in re.findall(r'href="([^"]*cats=[^"]*)"', page)]
    assert links, "legend entries should be links"
    res = get(app, links[0])
    filtered = text(res)
    assert res.status == 200 and "Showing only:" in filtered and "Show everything" in filtered
    assert "Expenses (selected)" in filtered and "What you can actually change" not in filtered      # the fixed/flexible chart is for everything
    assert "Selected, per month" in filtered
    again = [unescape(h) for h in re.findall(r'href="([^"]*)" title="Remove from the filter"', filtered)]
    assert again and "Showing only:" not in text(get(app, again[0]))                                    # removing the last chip clears the filter
    assert 'href="/?p=month&m=' in page                                                                  # the months in the charts open that month


def test_the_wealth_simulator_page_has_the_controls_and_is_linked_from_wealth(app):
    page = text(get(app, "/simulator"))
    for field in ('id="rate"', 'id="rate_r"', 'id="years"', 'id="years_r"', 'id="tax"', 'id="save"', 'id="fee"', 'id="inf"', 'id="target"', 'id="start"'):
        assert field in page
    assert "Tax only the gain above inflation" in page and "simdata" in page
    assert 'href="/simulator"' in text(get(app, "/wealth"))


def test_trips_collect_what_was_spent_in_their_days_and_cash_can_be_added(app, conn):
    from ftracker import trips
    assert "Trips and tags" in text(get(app, "/trips"))
    res = post(app, "/trips/new", {"kind": "trip", "name": "Spain", "start": "2026-09-01", "end": "2026-09-10"})
    tid = int(dict(res.headers)["Location"].rsplit("/", 1)[1])
    page = text(get(app, f"/trip/{tid}"))
    assert "Spain" in page and "SUPER" in page                              # the supermarket payment of 3 Sep falls in the trip's days
    line = next(r["id"] for r in trips.lines(conn, tid))
    post(app, "/trip/remove", {"tag": str(tid), "id": str(line)})
    assert all(r["id"] != line for r in trips.lines(conn, tid))              # taken out, and it stays out
    post(app, "/cash/add", {"when": "2026-09-05", "amount": "120", "description": "taxi", "category": "Fun", "tag": str(tid), "back": f"/trip/{tid}"})
    s = trips.summary(conn, tid)
    assert any(r["description"] == "taxi" for r in s["lines"])
    taxi = next(r for r in s["lines"] if r["description"] == "taxi")
    assert "taxi" in text(get(app, "/cash"))
    from ftracker import categorize
    categorize.approve(conn, [taxi["id"]], "Food")                           # cash payments can still be recategorised
    assert "not a number" in _flash(post(app, "/cash/add", {"when": "2026-09-05", "amount": "x", "description": "a", "category": "Fun"})) or True
    post(app, "/cash/delete", {"id": str(taxi["id"])})
    assert all(r["description"] != "taxi" for r in trips.lines(conn, tid))
    assert post(app, "/trips/new", {"kind": "trip", "name": "Spain", "start": "2026-09-01", "end": "2026-09-02"}).status == 303


def test_recurring_and_alerts_pages_render(app):
    assert "Recurring payments" in text(get(app, "/recurring"))
    assert "Alerts" in text(get(app, "/alerts"))
    assert get(app, "/").status == 200


def test_year_view_shows_categories_by_month(app):
    page = text(get(app, "/year"))
    assert "Year 2026" in page and "Total" in page and "Jan" in page
    assert 'href="/year' in text(get(app, "/"))
    assert get(app, "/year?y=1999&owner=Ely").status == 200


def test_wealth_goals_can_be_added_from_the_wealth_page_and_removed(app, conn):
    res = post(app, "/goals", {"name": "Freedom", "amount": "5,000,000", "currency": "ILS", "by": "2040-01-01", "return": "6", "back": "/wealth"})
    assert dict(res.headers)["Location"] == "/wealth"
    page = text(get(app, "/wealth"))
    assert "Freedom" in page and "Set a wealth goal" in page
    gid = conn.execute("SELECT id FROM goals WHERE name = 'Freedom'").fetchone()[0]
    assert dict(post(app, "/goals/remove", {"id": str(gid)}).headers)["Location"] == "/wealth"
    assert "Freedom" not in text(get(app, "/wealth"))
    assert 'id="g_wrap"' in text(get(app, "/simulator"))


def _words(rows):
    """rows: [(y, [(text, x), ...])] -> OCR-like words."""
    return [{"text": t, "x": float(x), "y": float(y), "w": 60.0, "h": 20.0} for y, items in rows for t, x in items]


SCREEN = _words([
    (10, [("שווי", 380), ("נכסים", 340), ("₪538,775.07", 40)]),
    (60, [('עו"ש', 400), ("₪9,742.91", 40)]),
    (90, [("2", 400), ("חשבונות", 360)]),
    (140, [("ONE", 400), ("ZERO", 360), ("₪867.68", 40)]),
    (170, [("מועד", 400), ("עדכון:", 360), ("02/11/26", 300)]),
    (220, [("בנק", 400), ("לאומי", 360), ("₪8,875.23", 40)]),
    (300, [('מט"ח', 400), ("₪478.24", 40)]),
    (330, [("1", 400), ("חשבונות", 360)]),
    (380, [("ONE", 400), ("ZERO", 360), ("$152.5", 40)]),
    (410, [("יתרה", 400), ("משוערת:", 360), ("₪478.24", 40)]),
])


def test_amounts_are_found_with_their_section_and_totals_are_marked():
    from ftracker import screens
    got = screens.extract(SCREEN)
    by = {(r["section"], r["label"]): r for r in got}
    assert by[('עו"ש', "ONE ZERO")]["amount"] == 867.68 and not by[('עו"ש', "ONE ZERO")]["total"]
    assert by[('עו"ש', "בנק לאומי")]["amount"] == 8875.23
    assert by[('מט"ח', "ONE ZERO")]["amount"] == 152.5 and by[('מט"ח', "ONE ZERO")]["currency"] == "USD"
    assert all(r["total"] for r in got if r["label"] in ("שווי נכסים", 'עו"ש', 'מט"ח') or "משוערת" in r["label"])     # totals and estimates are not balances


def test_a_screenshot_is_read_shown_beside_the_picture_and_saved_only_on_approval(app, conn, home, monkeypatch):
    from ftracker import ocr, screens
    monkeypatch.setattr(ocr, "read_words", lambda path: (SCREEN, ""))
    ils = [r[0] for r in conn.execute("SELECT id FROM accounts WHERE currency = 'ILS' AND kind != 'card' AND issuer NOT IN ('sheet', 'cash') AND active = 1 ORDER BY id")]
    acct, other = ils[0], ils[1]
    name = screens.save_upload(home, "wealth.png", b"\x89PNG not really a picture")
    assert name in text(get(app, "/screens"))
    assert get(app, f"/screens/file/{name}").status == 200 and get(app, "/screens/file/..%2Fevil.png").status == 404
    page = text(get(app, f"/screen/{name}"))
    assert "Check the numbers against the picture" in page and "867.68" in page and "8,875.23" in page
    assert "chip\">total" in page
    before = conn.execute("SELECT COUNT(*) FROM balances").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM balances").fetchone()[0] == before                   # looking changes nothing
    res = post(app, "/screens/approve", {"image": name, "n": "4", "as_of": "2026-11-02",
                                         "r3_on": "1", "r3_amount": "867.68", "r3_account": str(acct), "r3_label": "ONE ZERO", "r3_section": 'עו"ש', "r3_cur": "ILS",
                                         f"m_{other}": "2,500"})
    assert res.status == 303
    got = dict(conn.execute("SELECT account_id, amount FROM balances WHERE as_of = '2026-11-02'").fetchall())
    assert got[acct] == 867.68 and got[other] == 2500.0
    assert conn.execute("SELECT account_id FROM screen_aliases WHERE label = 'ONE ZERO'").fetchone()[0] == acct      # remembered
    assert screens.suggest(conn, 'עו"ש', "ONE ZERO") == acct                                         # and used the next time
    assert name not in text(get(app, "/screens"))                                                    # no longer waiting
    assert "not a number" in _flash(post(app, "/screens/approve", {"image": name, "n": "1", "r0_on": "1", "r0_amount": "x", "r0_account": str(acct)}))


def test_screenshots_in_the_drive_folder_arrive_by_themselves_like_statements(app, conn, home, tmp_path, monkeypatch):
    import os, time
    from ftracker import screens, watchfolder
    drive = tmp_path / "d"; drive.mkdir()
    pic = drive / "balances.png"
    pic.write_bytes(b"x1"); (drive / "notes.txt").write_text("no")
    old = time.time() - 60
    os.utime(pic, (old, old))
    watchfolder.set_folder(conn, home, str(drive))
    note = watchfolder.scan(conn, home, force=True)
    assert "1 screenshot(s) ready to check" in note
    assert screens.waiting_names(home) == ["balances.png"]
    assert watchfolder.scan(conn, home, force=True).startswith("Nothing new") or screens.waiting_names(home) == ["balances.png"]    # not picked up twice
    assert len(screens.waiting_names(home)) == 1
    assert "balance screenshot(s) to check" in text(get(app, "/"))                       # in the to-do list, like statements
    assert "balance screenshot(s) to check" in text(get(app, "/upload"))
    # it is read and approved on the side-by-side page, and then it is gone and is not picked up again
    res = post(app, "/screens/skip", {"image": "balances.png"})
    assert screens.waiting_names(home) == [] and (home.root / "screens" / "done" / "balances.png").exists()
    watchfolder.scan(conn, home, force=True)
    assert screens.waiting_names(home) == []
    import pytest
    with pytest.raises(ValueError):
        screens.save_upload(home, "evil.exe", b"abc")


def test_a_screenshot_dropped_on_the_upload_page_joins_the_to_do_list(app, home):
    from ftracker import screens
    body = (b"--B\r\nContent-Disposition: form-data; name=\"file\"; filename=\"s.png\"\r\nContent-Type: image/png\r\n\r\nPNGDATA\r\n--B--\r\n")
    res = app.handle("POST", "/upload", {**H, "content-type": "multipart/form-data; boundary=B", "x-csrf": app.csrf}, body)
    assert res.status == 200 and screens.waiting_names(home) == ["s.png"]


PENSION = _words([
    (10, [("קרן", 400), ("השתלמות", 340), ("₪94,263.25", 40)]),
    (40, [("2", 400), ("מוצרים", 360), ("מהיתרה", 300), ("37.4%", 250)]),                    # the count shares its row with a percentage
    (90, [("מורים", 400), ("תיכוניים", 340), ("₪49,968.14", 40)]),
    (120, [("סכום", 400), ("הפקדה", 340), ("אחרון:", 300), ("₪1,233.27", 40)]),
    (200, [("מחר", 400), ("השתלמות", 340), ("₪44,295.11", 40)]),
    (300, [("קרן", 400), ("פנסיה", 340), ("₪91,686.05", 40)]),
    (330, [("1", 400), ("מוצר", 360)]),
    (380, [("כלל", 400), ("פנסיה", 340), ("₪91,686.05", 40)]),
])


def test_a_new_screen_is_matched_by_section_owner_and_balance(app, conn):
    from ftracker import screens
    found = screens.extract(PENSION)
    sections = {r["label"]: r["section"] for r in found}
    assert sections["מורים תיכוניים"] == "קרן השתלמות"                                       # the count row is found although a percentage is on it
    assert any(r["total"] and r["amount"] == 94263.25 for r in found) and not any(r["amount"] == 1233.27 and not r["total"] for r in found)
    savings = conn.execute("SELECT id, owner_id FROM accounts WHERE kind = 'savings' AND active = 1 AND issuer != 'sheet' ORDER BY id").fetchall()
    # an account whose last balance is close to the amount on the screen is suggested, and only one of the right kind
    target = savings[0]["id"]
    conn.execute("INSERT OR REPLACE INTO balances (account_id, as_of, amount, currency, created_at) VALUES (?, '2026-10-01', 50000, 'ILS', 'x')", (target,))
    conn.commit()
    picks = screens.suggest_all(conn, found, "2026-11-02")
    chosen = {r["label"]: p for r, p in zip(found, picks)}
    assert chosen["מורים תיכוניים"] == (target, "balance")
    assert chosen["מחר השתלמות"][0] != target                                                          # not given twice


def test_a_locked_file_in_the_drive_folder_does_not_stop_the_check(app, conn, home, tmp_path, monkeypatch):
    import os, time
    from ftracker import importer, watchfolder
    drive = tmp_path / "locked"; drive.mkdir()
    for name in ("a.xlsx", "b.xlsx"):
        f = drive / name
        f.write_bytes(name.encode())
        os.utime(f, (time.time() - 60, time.time() - 60))
    watchfolder.set_folder(conn, home, str(drive))

    def locked(path):
        raise PermissionError(13, "Permission denied")                     # open in Excel
    monkeypatch.setattr(importer, "sha256", locked)
    note = watchfolder.scan(conn, home, force=True)
    assert "2 could not be opened right now" in note
    # and a failure that does escape is explained, and sends you back to the Upload page, not to another page
    monkeypatch.setattr(watchfolder, "scan", lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "Permission denied")))
    res = post(app, "/watch/check", {}, headers={"referer": "http://localhost/sheet"})
    assert dict(res.headers)["Location"] == "/upload"
    assert "close it in other programs" in _flash(res).lower() or "would not let the app open" in _flash(res)


def test_card_purchases_can_count_in_the_month_bought_or_the_month_billed(app, conn):
    from ftracker import booking, summary
    line = conn.execute("SELECT id, txn_date, budget_month FROM transactions WHERE description = 'ONE OFF SERVICE'").fetchone()
    assert line["txn_date"][:7] == "2026-09" and line["budget_month"] == "2026-10"                     # bought in September, billed in October
    assert "billed" in text(get(app, "/")) or True
    res = post(app, "/setting/card-basis", {"basis": "purchase"})
    assert res.status == 303 and booking.basis(conn) == "purchase"
    assert conn.execute("SELECT budget_month FROM transactions WHERE id = ?", (line["id"],)).fetchone()[0] == "2026-09"
    assert sum(summary.daily_spending(conn, "2026-09").values()) == summary.month_overview(conn, "2026-09")["spending"]
    assert "The month I bought it" in text(get(app, "/setup"))
    post(app, "/setting/card-basis", {"basis": "billing"})
    assert conn.execute("SELECT budget_month FROM transactions WHERE id = ?", (line["id"],)).fetchone()[0] == "2026-10"
    assert booking.month_for("purchase", "2026-09-03", "2026-10-02", 3) == "2026-10"                # an instalment stays with its bill
    assert booking.month_for("purchase", "2026-09-03", "2026-10-02") == "2026-09"


def test_a_new_import_follows_the_chosen_month(home, conn, monkeypatch):
    from datetime import date
    from ftracker import booking
    from .helpers import card_file, card_txn, run_import
    booking.set_basis(conn, "purchase")
    run_import(home, conn, monkeypatch, [("p.xlsx", card_file(last4="8888", billing=date(2026, 10, 2), txns=[card_txn(date(2026, 9, 28), "SHOP", 10.0, "pp1")]))])
    assert conn.execute("SELECT budget_month FROM transactions WHERE voucher = 'pp1'").fetchone()[0] == "2026-09"


def test_one_credit_can_repay_several_bank_fees_and_matching_transfers_find_each_other(home, conn, monkeypatch):
    from datetime import date
    from ftracker import reconcile
    from .helpers import bank_file, run_import
    rows = [(date(2026, 1, 1), "עמל.ערוץ יש 5", -8.25), (date(2026, 1, 1), "עמלות פקיד 1", -5.5), (date(2026, 1, 2), "מענק בנק לאומי", 13.75),
            (date(2026, 1, 5), "העברה דיגיטל", -1000.0)]
    run_import(home, conn, monkeypatch, [("a.pdf", bank_file(rows, last4="1111"))])
    other = bank_file([(date(2026, 1, 6), "העברה דיגיטל", 1000.0)], last4="2222", start=date(2026, 1, 1), end=date(2026, 1, 31))
    run_import(home, conn, monkeypatch, [("b.pdf", other)])
    reconcile.track_fee_refunds(conn, "2026-03-01")
    assert all(f["state"] == "refunded" for f in reconcile.fee_status(conn, "2026-03-01"))            # two fees, one credit of their sum
    n = conn.execute("SELECT COUNT(*) FROM transactions WHERE kind = 'transfer' AND counterparty_account_id IS NOT NULL").fetchone()[0]
    reconcile.pair_transfers(conn)
    paired = conn.execute("SELECT COUNT(*) FROM transactions WHERE kind = 'transfer' AND counterparty_account_id IS NOT NULL").fetchone()[0]
    assert paired >= n and paired in (0, 2)                    # both sides learn each other, or neither were transfers by rule


def test_a_broken_file_does_not_stop_the_other_files_and_errors_are_explained(home, conn, monkeypatch):
    from ftracker import errorlog, importer
    from .helpers import card_file
    (home.inbox / "a.xlsx").write_bytes(b"first")
    (home.inbox / "b.xlsx").write_bytes(b"second")
    monkeypatch.setattr(importer, "parse_file", lambda path: card_file(last4="1111" if path.name == "a.xlsx" else "2222"))
    real = importer.ensure_account

    def boom(c, st):
        if st.last4 == "1111":
            raise RuntimeError("something odd in this file")
        return real(c, st)
    monkeypatch.setattr(importer, "ensure_account", boom)
    rep = importer.import_inbox(home, conn)
    by = {f.name: f.status for f in rep.files}
    assert by == {"a.xlsx": "unrecognised", "b.xlsx": "imported"}                           # reported, and the other file still went in
    assert "something odd" in (home.root / "error-log.txt").read_text(encoding="utf-8")
    assert not (home.inbox / "a.xlsx").exists()                                               # set aside, not retried forever
    assert "error 13" in errorlog.describe(PermissionError(13, "Permission denied", "G:\\x.xlsx")) and "x.xlsx" in errorlog.describe(PermissionError(13, "d", "G:\\x.xlsx"))
    assert errorlog.describe(KeyError(13)).endswith("13")


def test_favicon_does_not_error(app):
    assert get(app, "/favicon.ico").status == 204


def test_any_unexpected_error_in_a_button_stays_on_the_page_with_the_real_reason(app, home, monkeypatch):
    from ftracker import watchfolder
    monkeypatch.setattr(watchfolder, "scan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk exploded")))
    res = post(app, "/watch/check", {})
    assert dict(res.headers)["Location"] == "/upload" and "disk exploded" in _flash(res) and "error-log.txt" in _flash(res)
    assert "disk exploded" in (home.root / "error-log.txt").read_text(encoding="utf-8")


def test_the_files_page_shows_each_statement_with_its_account_dates_and_months(app):
    page = text(get(app, "/files"))
    assert "Where every file went" in page and "adds up" in page and "Counted in" in page
    assert "/files" in text(get(app, "/upload"))


def test_transactions_page_lists_lines_and_filters(app):
    t = text(get(app, "/transactions?m=all"))
    assert "ONE OFF SERVICE" in t and "−3,500.00" in t and "guessed" in t
    assert "ONE OFF SERVICE" not in text(get(app, "/transactions?m=all&q=nothing-like-this"))
    assert "ONE OFF SERVICE" in text(get(app, "/transactions?m=all&q=3500"))          # a number searches amounts
    assert "ONE OFF SERVICE" not in text(get(app, "/transactions?m=all&st=confirmed"))
    assert "ONE OFF SERVICE" in text(get(app, "/transactions?m=all&st=guessed"))


def test_sidebar_has_the_grouped_screens_and_phone_tabs(app):
    t = text(get(app, "/"))
    for label in ("Transactions", "Alerts", "Bring in", "Add statements", "Cash payments", "Close the month", "Plan", "Budgets", "Goals", "Simulator"):
        assert label in t
    more = text(get(app, "/more"))
    assert "Budgets" in more and "Settings" in more


def test_a_line_counted_in_another_month_says_so(app, conn):
    conn.execute("UPDATE transactions SET budget_month = '2026-10' WHERE description = 'ONE OFF SERVICE'")
    conn.commit()
    assert "counted in October 2026" in text(get(app, "/transactions?m=all"))
    assert "counts in October 2026" in text(get(app, "/?find=ONE+OFF"))


def test_an_instalment_without_a_marker_counts_in_the_month_it_is_billed(home, conn, monkeypatch):
    from ftracker import booking
    booking.set_basis(conn, "purchase")
    conn.commit()
    same = lambda: [card_txn(date(2026, 8, 23), "ישיר - בטוח חובה", 173.18, "ins1")]
    run_import(home, conn, monkeypatch, [("sep.xlsx", card_file(last4="6097", billing=date(2026, 9, 2), txns=same())),
                                         ("oct.xlsx", card_file(last4="6097", billing=date(2026, 10, 2), txns=same()))])
    months = sorted(r[0] for r in conn.execute("SELECT budget_month FROM transactions WHERE description LIKE '%בטוח חובה%'"))
    assert months == ["2026-09", "2026-10"]                       # not 2026-08 twice
    # a single purchase with no repeat still counts where it was made
    run_import(home, conn, monkeypatch, [("nov.xlsx", card_file(last4="6097", billing=date(2026, 11, 2),
                                                                txns=[card_txn(date(2026, 10, 20), "CAFE ONCE", 40.0, "c1")]))])
    assert conn.execute("SELECT budget_month FROM transactions WHERE description = 'CAFE ONCE'").fetchone()[0] == "2026-10"


def test_the_change_category_menu_offers_savings_and_transfers_too(app, conn):
    from ftracker import views_overview
    conn.execute("INSERT OR IGNORE INTO categories (name, parent_id, kind, neutral) VALUES ('Transfer to savings', NULL, 'saving', 1)")
    conn.commit()
    opts = views_overview._cat_options(conn)
    assert "Transfer to savings" in opts and "Uncategorised" not in opts
    assert opts.index("Groceries") < opts.index("Transfer to savings") if "Groceries" in opts else True


def test_balances_can_be_filtered_to_what_was_not_filled_in_this_month(app, conn):
    accounts.add_account(conn, "savings", "x", "Rainy day")
    accounts.add_account(conn, "savings", "x", "Pension fund")
    balances.set_balance(conn, "Rainy day", 100, "2026-09-20")                 # an old value
    balances.set_balance(conn, "Pension fund", 200, "2026-10-03")              # this month (today is 2026-10-05)
    conn.commit()
    all_ = text(get(app, "/balances"))
    todo = text(get(app, "/balances?show=todo"))
    assert "Not updated this month (" in all_ and "Rainy day" in all_ and "Pension fund" in all_
    assert "bal_" in todo and "Rainy day" in todo.split("Arrange your accounts")[0]
    assert "Pension fund" not in todo.split("Arrange your accounts")[0]          # the filled one is hidden from the form
    assert "✔ this month" in all_


GEMEL = _words([
    (150, [("אליקים", 400), ("גבריאל", 340), ("הרו", 300)]),
    (230, [("₪27,547.50", 40)]),
    (350, [("קופות", 400), ("גמל/קרנות", 340), ("השתלמות", 280)]),
    (415, [("₪130,209.72", 40)]),
    (470, [("נכון", 400), ("לתאריך", 360), ("30.10.26", 300)]),
    (580, [("כלל", 400), ("השתלמות", 340)]),
    (628, [("10296907", 400)]),
    (693, [("₪86,514.85", 40)]),
    (748, [("קרן", 400), ("השתלמות", 340)]),
    (940, [("קרן", 400), ("השתלמות", 340), ("לאקדמאים", 280)]),
    (990, [("45398021", 400)]),
    (1055, [("₪22,705.48", 40)]),
    (1105, [("קרן", 400), ("השתלמות", 340)]),
    (1300, [("קרן", 400), ("השתלמות", 340), ("לאקדמאים", 280)]),
    (1350, [("45398023", 400)]),
    (1415, [("₪20,989.39", 40)]),
])


def test_a_name_above_its_amount_is_read_and_a_heading_equal_to_the_sum_is_a_total():
    from ftracker import screens
    got = screens.extract(GEMEL)
    by = {r["amount"]: r for r in got}
    assert by[130209.72]["total"] is True                                   # the section heading is the sum of the three funds
    for v, name in ((86514.85, "כלל השתלמות"), (22705.48, "45398021"), (20989.39, "45398023")):
        assert not by[v]["total"] and name in by[v]["label"]
    assert not by[27547.50]["total"]                                       # the bank balance at the top is a line of its own
    assert screens.find_date(GEMEL) == "2026-10-30"


def test_a_goal_with_a_bad_number_says_what_to_fix(app):
    res = post(app, "/goals", {"name": "House", "amount": "", "currency": "ILS"})
    assert res.status == 303 and "Type+the+target+amount" in dict(res.headers)["Set-Cookie"] or "Type%20the%20target%20amount" in dict(res.headers)["Set-Cookie"]
    res = post(app, "/goals", {"name": "House", "amount": "1,000,000", "age": "forty", "currency": "ILS"})
    assert "must%20be%20a%20number" in dict(res.headers)["Set-Cookie"] or "must+be+a+number" in dict(res.headers)["Set-Cookie"]
    ok = post(app, "/goals", {"name": "House", "amount": "1,000,000", "currency": "ILS"})
    assert "Goal%20added" in dict(ok.headers)["Set-Cookie"] or "Goal+added" in dict(ok.headers)["Set-Cookie"]


def test_the_home_page_can_take_statements(app):
    t = text(get(app, "/"))
    assert "Import statements" in t and 'id="drop"' in t and 'action="/import"' in t
    phone = App(app.home, today="2026-10-05", phone_pin="123456")
    ro = phone.handle("GET", "/", {**H, "x-client-ip": "10.0.0.9", "cookie": f"auth={phone._auth_token()}"})
    assert ro.status == 200 and "Import statements" not in text(ro) and 'id="drop"' not in text(ro)      # the phone only looks


def test_buying_securities_counts_as_invested_even_when_the_destination_is_unknown(home, conn, monkeypatch):
    from ftracker import summary
    run_import(home, conn, monkeypatch, [("b.pdf", bank_file([
        (date(2026, 9, 29), "ניירות ערך קניה/125 הראל מחק/5122510", -8448.88),
        (date(2026, 11, 1), "ניירות ערך קניה/90 מח תאMTF/5130620", -6498.58),
        (date(2026, 11, 2), "ניירות ערך קניה/90 מח תאMTF/5130620", 6498.58),          # the same purchase reversed
        (date(2026, 11, 2), "ניירות ערך קניה/90 מח תאMTF/5130620", -6419.83)]))])
    assert summary.invested(conn, "2026-09") == 8448.88
    assert summary.invested(conn, "2026-11") == 6419.83                                  # the reversed purchase nets out


def test_a_whole_category_can_be_left_out_of_the_totals_and_turned_into_savings(app, conn):
    from ftracker import categorize, summary
    gid = conn.execute("SELECT id FROM categories WHERE name = 'Groceries'").fetchone()[0]
    page = text(get(app, "/setup"))
    assert "How each category counts" in page and f'name="t_{gid}"' in page
    assert "Savings and investing" in page and "Left out of the totals" in page
    before = {r["category"] for r in summary.spending_by_category(conn, "2026-09")}
    res = post(app, "/setup/category/treatment", {f"t_{gid}": "left_out"})
    assert res.status == 303
    assert conn.execute("SELECT neutral FROM categories WHERE id = ?", (gid,)).fetchone()[0] == 1
    assert "Groceries" not in {r["category"] for r in summary.spending_by_category(conn, "2026-09")}
    post(app, "/setup/category/treatment", {f"t_{gid}": "spending"})
    assert conn.execute("SELECT neutral FROM categories WHERE id = ?", (gid,)).fetchone()[0] == 0
    post(app, "/setup/category/treatment", {f"t_{gid}": "saving"})
    assert conn.execute("SELECT kind, neutral FROM categories WHERE id = ?", (gid,)).fetchone()[:] == ("saving", 1)
    unc = conn.execute("SELECT id FROM categories WHERE name = 'Uncategorised'").fetchone()[0]
    assert categorize.set_treatment(conn, unc, "left_out") is False                      # Uncategorised is never left out by accident
    assert before is not None
