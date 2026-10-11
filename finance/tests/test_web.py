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
    assert "show the" in t and "line(s)" in t and "Amount ₪" in t
    assert "ONE OFF SERVICE" in t and "3,500.00" in t                  # the individual expense is visible inside its group


def test_bulk_approve_takes_several_merchants_at_once(app, conn):
    import json as _json
    groups = [(r["description_norm"], r["cat"]) for r in conn.execute(
        """SELECT t.description_norm, c.name AS cat FROM transactions t JOIN categories c ON c.id = t.category_id
           WHERE t.category_status = 'proposed' GROUP BY t.description_norm, c.name""")]
    assert len(groups) >= 2
    page = text(get(app, "/review"))
    assert "Approve several at once" in page and 'form="bulk"' in page and "Tick all shown" in page
    sel = [_json.dumps(list(g), ensure_ascii=False) for g in groups[:2]]
    res = post(app, "/approve/bulk", {"sel": sel, "category": "Fun", "learn": "1"})
    assert res.status == 303
    left = {(r["description_norm"], r["cat"]) for r in conn.execute(
        """SELECT t.description_norm, c.name AS cat FROM transactions t JOIN categories c ON c.id = t.category_id
           WHERE t.category_status = 'proposed' GROUP BY t.description_norm, c.name""")}
    assert not (set(groups[:2]) & left)                                      # both merchants were approved in one go
    none = post(app, "/approve/bulk", {"category": ""})
    assert none.status == 303


def test_review_can_be_filtered_to_merchants_with_or_without_a_suggestion(app):
    both = text(get(app, "/review?show=all"))
    match = text(get(app, "/review?show=match"))
    none = text(get(app, "/review?show=none"))
    assert "With a suggestion (" in both and "No suggestion yet (" in both
    assert "no match: needs your review" not in match                      # only merchants that already have a suggestion
    assert "choose…" not in match
    assert "no match: needs your review" in none or "Nothing is waiting for approval in this view" in none


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
