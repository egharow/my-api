from datetime import date

from ftracker import categorize, splits
from .helpers import bank_file, run_import
from .test_web import app, get, post, text  # noqa: F401


def _bill(home, conn, monkeypatch, amounts):
    rows = [(date(2026, 9, 2 + i), "עיריית דוגמה גביה", -a) for i, a in enumerate(amounts)]
    run_import(home, conn, monkeypatch, [("b.pdf", bank_file(rows))])
    return {r["amount"]: r["id"] for r in conn.execute("SELECT id, amount FROM transactions WHERE description LIKE '%דוגמה%'")}


def _cats(conn):
    categorize.add_category(conn, "Arnona", "spending"); categorize.add_category(conn, "Water", "spending"); conn.commit()


def test_a_payment_can_be_split_and_the_parts_add_up(home, conn, monkeypatch):
    _cats(conn)
    ids = _bill(home, conn, monkeypatch, [1234.5])
    tid = ids[-1234.5]
    child = splits.split_line(conn, tid, 864, "Arnona", "Water")
    conn.commit()
    parts = conn.execute("SELECT amount, category_id, statement_id, split_of, split_total FROM transactions WHERE id IN (?, ?) ORDER BY id", (tid, child)).fetchall()
    assert [p["amount"] for p in parts] == [-864.0, -370.5] and round(sum(p["amount"] for p in parts), 2) == -1234.5
    assert parts[0]["split_total"] == -1234.5 and parts[1]["split_of"] == tid and parts[0]["statement_id"] == parts[1]["statement_id"]
    names = {r["name"] for r in conn.execute("SELECT c.name FROM transactions t JOIN categories c ON c.id = t.category_id WHERE t.id IN (?, ?)", (tid, child))}
    assert names == {"Arnona", "Water"}
    from ftracker import summary
    spent = {r["category"]: r["spent"] for r in summary.spending_by_category(conn, conn.execute("SELECT budget_month FROM transactions WHERE id = ?", (tid,)).fetchone()[0])}
    assert spent["Arnona"] == 864.0 and spent["Water"] == 370.5                         # the totals count the two parts, not the whole twice
    try:
        splits.split_line(conn, tid, 100, "Arnona", "Water")
    except ValueError as exc:
        assert "already split" in str(exc)
    assert splits.unsplit(conn, child) is True
    row = conn.execute("SELECT amount, split_total, category_status FROM transactions WHERE id = ?", (tid,)).fetchone()
    assert row["amount"] == -1234.5 and row["split_total"] is None and row["category_status"] == "proposed"
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE split_of IS NOT NULL").fetchone()[0] == 0


def test_a_split_rule_applies_to_past_and_new_payments_and_never_twice(home, conn, monkeypatch):
    _cats(conn)
    ids = _bill(home, conn, monkeypatch, [1234.5, 864.0, 500.0])
    splits.add_rule(conn, "עיריית דוגמה", 864, "Arnona", "Water")
    done = splits.apply_to_all(conn); conn.commit()
    assert done == {"split": 1, "whole": 1}                                              # 1,234.50 splits, 864 is all Arnona, 500 is smaller so untouched
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE split_of IS NOT NULL").fetchone()[0] == 1
    assert conn.execute("SELECT c.name FROM transactions t JOIN categories c ON c.id = t.category_id WHERE t.id = ?", (ids[-864.0],)).fetchone()[0] == "Arnona"
    assert conn.execute("SELECT split_total FROM transactions WHERE id = ?", (ids[-500.0],)).fetchone()[0] is None
    assert splits.apply_to_all(conn) == {"split": 0, "whole": 0}                         # a second pass does nothing more
    # a new import of another bill is split by itself
    run_import(home, conn, monkeypatch, [("c.pdf", bank_file([(date(2026, 11, 3), "עיריית דוגמה גביה", -1100.0)], start=date(2026, 10, 1), end=date(2026, 11, 30), opening=5000.0))])
    new = conn.execute("SELECT id FROM transactions WHERE txn_date = '2026-11-03' AND split_total IS NOT NULL").fetchone()
    assert new is not None
    assert conn.execute("SELECT amount FROM transactions WHERE split_of = ?", (new["id"],)).fetchone()[0] == -236.0


def test_the_same_statement_imported_again_does_not_duplicate_a_split_payment(home, conn, monkeypatch):
    _cats(conn)
    _bill(home, conn, monkeypatch, [1234.5])
    splits.add_rule(conn, "עיריית דוגמה", 864, "Arnona", "Water"); splits.apply_to_all(conn); conn.commit()
    before = conn.execute("SELECT COUNT(*), ROUND(SUM(amount), 2) FROM transactions WHERE description LIKE '%דוגמה%'").fetchone()
    _bill_again = [(date(2026, 9, 2), "עיריית דוגמה גביה", -1234.5)]
    run_import(home, conn, monkeypatch, [("again.pdf", bank_file(_bill_again))])                  # the same movement arrives from another file
    after = conn.execute("SELECT COUNT(*), ROUND(SUM(amount), 2) FROM transactions WHERE description LIKE '%דוגמה%'").fetchone()
    assert tuple(before) == tuple(after) == (2, -1234.5)


def test_settings_and_transactions_offer_splitting(app, conn):
    page = text(get(app, "/setup"))
    assert "Bills that are two things" in page and 'name="pattern"' in page and "A new category" in page
    res = post(app, "/setup/category/new", {"name": "Water", "treatment": "spending"})
    assert res.status == 303 and conn.execute("SELECT kind FROM categories WHERE name = 'Water'").fetchone()[0] == "expense"
    post(app, "/setup/category/new", {"name": "Water", "treatment": "spending"})                    # a duplicate is refused, nothing breaks
    t = text(get(app, "/transactions?m=all"))
    assert "split into two categories" in t
    tid = conn.execute("SELECT id FROM transactions WHERE amount < -3000").fetchone()[0]
    post(app, "/line/split", {"id": str(tid), "first": "1000", "cat1": "Groceries", "cat2": "Water", "back": "/transactions?m=all"})
    t2 = text(get(app, "/transactions?m=all"))
    assert "split: first part" in t2 and "split: the rest" in t2 and "Undo the split" in t2
    post(app, "/line/unsplit", {"id": str(tid), "back": "/transactions?m=all"})
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE split_of IS NOT NULL").fetchone()[0] == 0
