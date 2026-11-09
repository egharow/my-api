import json

from ftracker import checks, closing

from .test_web import app, get, post, text, H  # noqa: F401


def _month(conn, tid):
    return conn.execute("SELECT budget_month FROM transactions WHERE id = ?", (tid,)).fetchone()[0]


def _ids(conn, desc_like):
    return [r[0] for r in conn.execute("SELECT id FROM transactions WHERE description LIKE ?", (f"%{desc_like}%",))]


def test_only_spending_is_listed_and_the_latest_month_is_the_default(app, conn):
    months = checks.months(conn)
    assert months and months[0]["month"] == checks.default_month(conn)
    listed = {r["description"] for r in checks.lines(conn, None, "all")}
    assert "ONE OFF SERVICE" in listed
    assert not any("מאסטרקרד" in d for d in listed)                 # a card payment is not an expense that happened
    ids = _ids(conn, "ONE OFF")
    conn.execute("UPDATE transactions SET left_out = 1 WHERE id = ?", (ids[0],)); conn.commit()
    assert "ONE OFF SERVICE" not in {r["description"] for r in checks.lines(conn, None, "all")}      # left out of the totals, so not checked


def test_checking_a_line_is_separate_from_its_category_and_can_be_undone(app, conn):
    tid = _ids(conn, "ONE OFF")[0]
    before = conn.execute("SELECT category_status FROM transactions WHERE id = ?", (tid,)).fetchone()[0]
    res = post(app, "/check/mark", {"id": str(tid), "state": "ok", "back": "/check"})
    assert res.status == 303
    row = conn.execute("SELECT check_state, check_by, check_at, category_status FROM transactions WHERE id = ?", (tid,)).fetchone()
    assert row["check_state"] == "ok" and row["check_by"] and row["check_at"] and row["category_status"] == before      # the category is untouched
    m = _month(conn, tid)
    assert "ONE OFF SERVICE" not in text(get(app, f"/check?m={m}"))                                                     # no longer in the to-do list
    assert "ONE OFF SERVICE" in text(get(app, f"/check?m={m}&show=ok"))
    post(app, "/check/mark", {"id": str(tid), "state": "", "back": "/check"})
    assert conn.execute("SELECT check_state FROM transactions WHERE id = ?", (tid,)).fetchone()[0] is None
    assert "ONE OFF SERVICE" in text(get(app, f"/check?m={m}"))
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'check_expenses'").fetchone()[0] >= 2          # who did it is recorded


def test_flagging_needs_a_reason_and_lands_in_the_flagged_section(app, conn):
    tid = _ids(conn, "ONE OFF")[0]
    bad = post(app, "/check/mark", {"id": str(tid), "state": "question", "back": "/check"})
    assert bad.status == 303 and conn.execute("SELECT check_state FROM transactions WHERE id = ?", (tid,)).fetchone()[0] is None
    post(app, "/check/mark", {"id": str(tid), "state": "question", "reason": "not_mine", "note": "never been there", "back": "/check"})
    assert checks.flagged_count(conn) == 1
    page = text(get(app, "/check"))
    assert 'id="flagged"' in page and "Flagged · 1" in page and "I did not make this payment" in page and "never been there" in page
    assert "It is fine" in page and "Leave out of totals" in page and "Back to unchecked" in page
    # a flagged line stays in the totals until you leave it out yourself
    from ftracker import summary
    assert any(r["category"] for r in summary.spending_by_category(conn, _month(conn, tid)))
    post(app, "/check/mark", {"id": str(tid), "state": "ok", "back": "/check"})                                        # "It is fine"
    assert checks.flagged_count(conn) == 0


def test_many_lines_at_once_and_the_fetch_version_answers_in_json(app, conn):
    ids = [r["id"] for r in checks.lines(conn, checks.default_month(conn), "todo")]
    assert len(ids) >= 1
    body = {"id": [str(i) for i in ids], "state": "ok", "back": "/check"}
    from urllib.parse import urlencode
    res = app.handle("POST", "/check/mark", {**H, "x-ajax": "1"}, urlencode({"csrf": app.csrf, **body}, doseq=True).encode())
    assert res.status == 200 and json.loads(res.body)["ok"] is True and json.loads(res.body)["changed"] == len(ids)
    assert checks.todo_count(conn) == 0
    assert "Everything shown is checked" in text(get(app, "/check"))


def test_months_view_and_sidebar_and_home_chip(app, conn):
    page = text(get(app, "/check?view=months"))
    assert "Expenses" in page and "To check" in page and "2026-" not in page.split("<table")[0]
    home = text(get(app, "/"))
    assert "expenses to check in" in home                                  # the to-do chip
    assert "Check expenses" in home and "Bring in" in home
    tid = _ids(conn, "ONE OFF")[0]
    post(app, "/check/mark", {"id": str(tid), "state": "question", "reason": "ask", "back": "/check"})
    assert "flagged expense" in text(get(app, "/"))


def test_transactions_shows_and_filters_by_check_and_close_month_only_warns(app, conn):
    tid = _ids(conn, "ONE OFF")[0]
    post(app, "/check/mark", {"id": str(tid), "state": "ok", "back": "/check"})
    t = text(get(app, "/transactions?m=all"))
    assert "✓ checked" in t
    assert "ONE OFF SERVICE" in text(get(app, "/transactions?m=all&ck=ok"))
    assert "ONE OFF SERVICE" not in text(get(app, "/transactions?m=all&ck=todo"))
    m = _month(conn, tid)
    cs = closing.checks(conn, m, "2026-11-05")
    item = [c for c in cs if c["label"] == "Expenses are checked"]
    assert item and (item[0]["ok"] or "You can still close the month" in item[0]["detail"])
    res = post(app, "/close/do", {"m": m})
    assert res.status == 303 and m in closing.closed_months(conn)                                        # never a blocker
