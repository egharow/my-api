import pytest

from ftracker import accounts, alerts, regulars
from .test_web import app, get, post, text  # noqa: F401


def _line(conn, month, day, desc, amount, key):
    aid = conn.execute("SELECT id FROM accounts WHERE label = 'Leumi 7777'").fetchone()[0]
    bid = conn.execute("SELECT id FROM batches WHERE note = 'regulars'").fetchone()[0]
    cat = conn.execute("SELECT id FROM categories WHERE name = ?", ("Other income" if amount > 0 else "Property tax",)).fetchone()[0]
    conn.execute("""INSERT INTO transactions (batch_id, account_id, dedupe_key, txn_date, budget_month, description, description_norm, amount, currency,
                    category_id, category_status, kind, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (bid, aid, key, f"{month}-{day:02d}", month, desc, desc.upper().replace('"', " ").replace("-", " ").strip(), amount, "ILS", cat, "approved",
                  "refund" if amount > 0 else "purchase", "x"))


@pytest.fixture
def lines(conn):
    accounts.add_account(conn, "bank", "manual", "Leumi 7777")
    conn.execute("INSERT INTO batches (created_at, status, note) VALUES ('x', 'committed', 'regulars')")
    for i, m in enumerate(["2026-06", "2026-07", "2026-08", "2026-09"]):
        _line(conn, m, 22, "קצבת ילדים-י", 276.0, f"c{i}")
        _line(conn, m, 5, "ארנונה עירייה", -864.0, f"a{i}")
    conn.commit()
    return conn


def _status(conn, name, today, month=None):
    for e in regulars.evaluate(conn, today):
        if e["item"]["name"] == name and e["month"] == (month or today[:7]):
            return e
    raise AssertionError("not evaluated")


def test_money_that_should_come_every_month_is_tracked_and_flagged(lines):
    conn = lines
    regulars.add(conn, "קצבת ילדים", "קצבת ילדים", "in"); conn.commit()
    assert _status(conn, "קצבת ילדים", "2026-10-10")["status"] == "not yet"        # before its usual day
    assert _status(conn, "קצבת ילדים", "2026-10-24")["status"] == "not yet"        # a few days late is still fine
    e = _status(conn, "קצבת ילדים", "2026-10-30")
    assert e["status"] == "missing" and e["usual"] == 276.0 and e["day"] == 22     # past its day plus the grace
    _line(conn, "2026-10", 22, "קצבת ילדים-י", 276.0, "c9"); conn.commit()
    assert _status(conn, "קצבת ילדים", "2026-10-30")["status"] == "ok"


def test_an_amount_different_from_usual_is_flagged_but_small_changes_are_not(lines):
    conn = lines
    regulars.add(conn, "קצבת ילדים", "קצבת ילדים", "in"); conn.commit()
    _line(conn, "2026-10", 22, "קצבת ילדים-י", 281.0, "c9"); conn.commit()
    assert _status(conn, "קצבת ילדים", "2026-10-30")["status"] == "ok"             # under the allowed difference
    conn.execute("UPDATE transactions SET amount = 330 WHERE dedupe_key = 'c9'"); conn.commit()
    e = _status(conn, "קצבת ילדים", "2026-10-30")
    assert e["status"] == "different" and e["this"] == 330.0 and e["diff"] == 54.0


def test_a_fixed_amount_you_give_beats_history_and_expenses_work_too(lines):
    conn = lines
    regulars.add(conn, "ארנונה", "ארנונה", "out", amount=1000.0); conn.commit()
    _line(conn, "2026-10", 5, "ארנונה עירייה", -864.0, "a9"); conn.commit()
    e = _status(conn, "ארנונה", "2026-10-30")
    assert e["usual"] == 1000.0 and e["status"] == "different"                      # 864 against the 1,000 you said


def test_flags_show_up_as_alerts_the_overview_and_the_recurring_page(app, conn):
    accounts.add_account(conn, "bank", "manual", "Leumi 7777")
    conn.execute("INSERT INTO batches (created_at, status, note) VALUES ('x', 'committed', 'regulars')")
    for i, m in enumerate(["2026-06", "2026-07", "2026-08", "2026-09"]):
        _line(conn, m, 22, "קצבת ילדים-י", 276.0, f"c{i}")
    conn.commit()
    res = post(app, "/regular/add", {"name": "קצבת ילדים", "pattern": "", "direction": "in", "amount": "", "tolerance": "8"})
    assert res.status == 303 and len(regulars.items(conn)) == 1
    dup = post(app, "/regular/add", {"name": "קצבת ילדים", "pattern": "", "direction": "in"})
    assert len(regulars.items(conn)) == 1                                                 # a duplicate is refused
    page = text(get(app, "/recurring"))
    assert "Expected every month" in page and "קצבת ילדים" in page and 'id="expected"' in page
    found = alerts.build(conn, "2026-10-30")
    assert any("has not arrived" in a["title"] and "קצבת ילדים" in a["title"] for a in found)
    assert "Expected this month" in text(get(app, "/"))
    post(app, "/regular/remove", {"id": str(regulars.items(conn)[0]["id"])})
    assert regulars.items(conn) == []


def test_the_app_suggests_what_it_sees_every_month(lines):
    conn = lines
    sug = regulars.suggestions(conn, "2026-09", ["2026-06", "2026-07", "2026-08", "2026-09"])
    names = {(s["name"], s["direction"]) for s in sug}
    assert ("קצבת ילדים-י", "in") in names and ("ארנונה עירייה", "out") in names
    regulars.add(conn, "קצבת ילדים", sug[0]["pattern"] if sug[0]["direction"] == "in" else "קצבת ילדים", "in"); conn.commit()
    after = {(s["name"], s["direction"]) for s in regulars.suggestions(conn, "2026-09", ["2026-06", "2026-07", "2026-08", "2026-09"])}
    assert ("קצבת ילדים-י", "in") not in after                                              # what you track is not suggested again
