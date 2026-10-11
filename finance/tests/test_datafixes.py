import json
from datetime import date

from ftracker import accounts, balances, datafixes, fx


def test_fixes_apply_once_and_keep_history(conn, home, tmp_path):
    for label, kind in (("הראל", "savings"), ("גשר", "savings"), ("BNC", "real_estate"), ("One Zero", "savings"), ("וואן זירו", "bank")):
        accounts.add_account(conn, kind, "manual", label)
    balances.set_balance(conn, "הראל", 100.0, "2026-01-01")
    balances.set_balance(conn, "גשר", 50.0, "2026-01-01")
    balances.set_balance(conn, "BNC", 250000.0, "2026-01-01")
    balances.set_balance(conn, "BNC", 250000.0, "2026-03-01")
    fx.set_rate(conn, "2026-03-01", "USD", "ILS", 3.0, "manual")
    app = tmp_path / "app"; app.mkdir()
    (app / "data_fixes.json").write_text(json.dumps([
        {"id": "a", "action": "rename_account", "label": "הראל", "to": "מחר"},
        {"id": "b", "action": "close_account", "label": "גשר", "on": "2026-02-01"},
        {"id": "c", "action": "merge_accounts", "from": "One Zero", "into": "וואן זירו"},
        {"id": "d", "action": "set_balances", "label": "BNC", "amount": 60000, "currency": "USD"},
        {"id": "e", "action": "delete_account", "label": "no such"},
    ]), encoding="utf-8")
    assert len(datafixes.pending(conn, app)) == 5
    lines = datafixes.apply(conn, home, app, date(2026, 10, 8))
    assert len(lines) == 5 and any(l.startswith("skipped") for l in lines)
    assert conn.execute("SELECT COUNT(*) FROM accounts WHERE label = 'מחר'").fetchone()[0] == 1
    assert datafixes.apply(conn, home, app, date(2026, 10, 8)) == []                          # each fix runs once
    jan = balances.net_worth(conn, "2026-01-15", "ILS")["total"]
    mar = balances.net_worth(conn, "2026-03-15", "ILS")["total"]
    assert abs(jan - (100 + 50 + 60000 * 3.0)) < 0.01                                        # closed account still counts before it closed
    assert abs(mar - (100 + 60000 * 3.0)) < 0.01                                             # and not after; BNC is 60,000 dollars at the current rate
    assert any(f.endswith("before-data-fixes.db") for f in map(str, home.backups.iterdir()))


def test_moving_accounts_keeps_the_order(conn):
    for label in ("א", "ב", "ג"):
        accounts.add_account(conn, "savings", "manual", label)
    ids = {r["label"]: r["id"] for r in conn.execute("SELECT id, label FROM accounts WHERE kind = 'savings'")}
    accounts.move_account(conn, ids["ג"], "up")
    accounts.move_account(conn, ids["ג"], "up")
    order = [r["label"] for r in conn.execute("SELECT label FROM accounts WHERE kind = 'savings' ORDER BY sort_order, kind, label")]
    assert order == ["ג", "א", "ב"]
    accounts.move_account(conn, ids["ג"], "up")                                               # already first: nothing changes
    assert [r["label"] for r in conn.execute("SELECT label FROM accounts WHERE kind = 'savings' ORDER BY sort_order, kind, label")] == order


def test_delete_refuses_accounts_with_statements_or_payments(conn):
    a = accounts.add_account(conn, "bank", "manual", "tmp")
    accounts.delete_account(conn, a)
    assert conn.execute("SELECT COUNT(*) FROM accounts WHERE id = ?", (a,)).fetchone()[0] == 0


def test_salary_fixes_split_income_by_person_and_apply_to_waiting_lines(conn, home, tmp_path):
    from ftracker import summary
    app = tmp_path / "app"; app.mkdir()
    (app / "data_fixes.json").write_text(json.dumps([
        {"id": "r", "action": "rename_category", "category": "Salary", "to": "Salary (Ely)"},
        {"id": "c", "action": "add_category", "name": "Salary (Shir)", "kind": "income"},
        {"id": "u", "action": "add_rule", "pattern": "חינוך-משכו", "category": "Salary (Shir)", "mode": "income"},
    ]), encoding="utf-8")
    datafixes.apply(conn, home, app, date(2026, 10, 8))
    names = {r[0] for r in conn.execute("SELECT name FROM categories WHERE kind = 'income'")}
    assert {"Salary (Ely)", "Salary (Shir)"} <= names
    assert summary.income_owner("Salary (Shir)") == "Shir" and summary.income_owner("Salary (Ely)") == "Ely" and summary.income_owner("Other income") is None
    conn.execute("INSERT INTO monthly_entries (month, section, label, actual, source, created_at) VALUES ('2025-04','income','Shir',12000,'sheet','x')")
    conn.execute("INSERT INTO monthly_entries (month, section, label, actual, source, created_at) VALUES ('2025-04','income','Ely',30000,'sheet','x')")
    conn.commit()
    both = summary.income_by_source(conn, ["2025-04"])
    assert both["2025-04"] == {"Salary (Shir)": 12000.0, "Salary (Ely)": 30000.0}
    assert summary.income_by_source(conn, ["2025-04"], "Shir")["2025-04"] == {"Salary (Shir)": 12000.0}


def test_account_arrangement_and_fold_line_are_saved_and_applied(conn):
    from ftracker import expected
    for label in ("א", "ב", "ג", "ד"):
        accounts.add_account(conn, "savings", "manual", label)
    ids = {r["label"]: r["id"] for r in conn.execute("SELECT id, label FROM accounts WHERE kind = 'savings'")}
    balances.set_arrangement(conn, [ids["ד"], ids["ג"], ids["ב"], ids["א"]], 2)
    pend = balances.pending(conn, "2026-10-10")
    assert [p["account"]["label"] for p in pend] == ["ד", "ג", "ב", "א"]
    assert [p["folded"] for p in pend] == [False, False, True, True]
    cl = expected.checklist(conn, "2026-10-10")
    item = [i for g in cl["groups"] for i in g["items"] if i.get("children")][0]
    assert "2 of 2 accounts" in item["detail"] and [k["folded"] for k in item["children"]] == [False, False, True, True]


def test_fixed_costs_and_unusual_payments(conn, home):
    from ftracker import insights
    conn.execute("INSERT INTO accounts (kind, issuer, label, last4, currency, created_at) VALUES ('card','isracard','C','1','ILS','x')")
    a = conn.execute("SELECT id FROM accounts WHERE kind='card'").fetchone()[0]
    conn.execute("INSERT INTO batches (status, created_at) VALUES ('draft','x')")
    b = conn.execute("SELECT id FROM batches").fetchone()[0]
    cat = conn.execute("SELECT id FROM categories WHERE name = 'Insurance'").fetchone()[0]
    fun = conn.execute("SELECT id FROM categories WHERE name = 'Fun'").fetchone()[0]
    months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    n = 0

    def add(m, desc, amt, c):
        nonlocal n
        n += 1
        conn.execute("""INSERT INTO transactions (batch_id, account_id, dedupe_key, txn_date, budget_month, description, description_norm, amount, currency,
                        category_id, category_status, kind, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (b, a, f"k{n}", f"{m}-05", m, desc, desc.upper(), -amt, "ILS", c, "approved", "purchase", "x"))
    for m in months:
        add(m, "PHOENIX INSURANCE", 300.0 if m != "2026-06" else 360.0, cat)
        add(m, "CAFE ONE", 40.0, fun)
        add(m, "CAFE TWO", 55.0, fun)
    add("2026-06", "WEDDING HALL DEPOSIT", 2500.0, fun)
    conn.commit()
    fixed = insights.fixed_costs(conn, "2026-06", months)
    ins = next(f for f in fixed if f["name"] == "PHOENIX INSURANCE")
    assert ins["usual"] == 300.0 and ins["this"] == 360.0 and ins["status"] == "changed"
    odd = insights.unusual(conn, "2026-06", months, None, fixed)
    assert [o["description"] for o in odd] == ["WEDDING HALL DEPOSIT"] and "large" in odd[0]["why"]


def test_loan_payment_rule_and_stale_transfer_flags_close(conn, home, tmp_path):
    import json as _json
    conn.execute("INSERT INTO accounts (kind, issuer, label, last4, currency, created_at) VALUES ('bank','leumi','L','1','ILS','x')")
    a = conn.execute("SELECT id FROM accounts WHERE kind='bank'").fetchone()[0]
    conn.execute("INSERT INTO batches (status, created_at) VALUES ('draft','x')")
    b = conn.execute("SELECT id FROM batches").fetchone()[0]
    internal = conn.execute("SELECT id FROM categories WHERE name = 'Internal transfer'").fetchone()[0]
    conn.execute("""INSERT INTO transactions (batch_id, account_id, dedupe_key, txn_date, budget_month, description, description_norm, amount, currency,
                    category_id, category_status, kind, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (b, a, "k1", "2026-09-01", "2026-09", "בנק הפועלים-י", "בנק הפועלים-י", -1000.0, "ILS", internal, "proposed", "transfer", "x"))
    tid = conn.execute("SELECT id FROM transactions").fetchone()[0]
    from ftracker import discrepancies as dx, reconcile
    dx.raise_item(conn, f"xfer:{tid}", "transfer_destination", "Transfer with no destination", "x", "info", ("transaction", tid), b)
    conn.commit()
    app = tmp_path / "app"; app.mkdir()
    (app / "data_fixes.json").write_text(_json.dumps([
        {"id": "l", "action": "add_rule", "pattern": "בנק הפועלים-י", "category": "Loans", "set_kind": "other", "direction": "out"}]), encoding="utf-8")
    datafixes.apply(conn, home, app, date(2026, 10, 11))
    row = conn.execute("SELECT c.name, t.category_status, t.kind FROM transactions t JOIN categories c ON c.id = t.category_id WHERE t.id = ?", (tid,)).fetchone()
    assert (row["name"], row["category_status"], row["kind"]) == ("Loans", "approved", "other")
    assert conn.execute("SELECT status FROM discrepancies WHERE fingerprint = ?", (f"xfer:{tid}",)).fetchone()[0] == "resolved"


def test_a_payment_moved_out_of_a_transfer_category_becomes_an_ordinary_expense(conn, home, tmp_path):
    import json as _json
    conn.execute("INSERT INTO accounts (kind, issuer, label, last4, currency, created_at) VALUES ('bank','leumi','L','1','ILS','x')")
    a = conn.execute("SELECT id FROM accounts WHERE kind='bank'").fetchone()[0]
    conn.execute("INSERT INTO batches (status, created_at) VALUES ('draft','x')")
    b = conn.execute("SELECT id FROM batches").fetchone()[0]
    household = conn.execute("SELECT id FROM categories WHERE name = 'Transfer to household'").fetchone()[0]
    conn.execute("""INSERT INTO transactions (batch_id, account_id, dedupe_key, txn_date, budget_month, description, description_norm, amount, currency,
                    category_id, category_status, kind, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (b, a, "k1", "2026-08-21", "2026-08", 'העברת זה"ב', 'העברת זהב', -42000.0, "ILS", household, "approved", "transfer", "x"))
    conn.commit()
    app = tmp_path / "app"; app.mkdir()
    (app / "data_fixes.json").write_text(_json.dumps([
        {"id": "d", "action": "delete_rule", "pattern": "העברת זה\"ב"},
        {"id": "r", "action": "recategorize_line", "description": "העברת זה\"ב", "date": "2026-08-21", "amount": -42000, "category": "Car"}]), encoding="utf-8")
    lines = datafixes.apply(conn, home, app, date(2026, 10, 11))
    assert any("moved to Car" in l for l in lines)
    row = conn.execute("SELECT c.name, t.kind FROM transactions t JOIN categories c ON c.id = t.category_id").fetchone()
    assert (row["name"], row["kind"]) == ("Car", "purchase")
    from ftracker import summary
    assert dict((r["category"], r["spent"]) for r in summary.spending_by_category(conn, "2026-08"))["Car"] == 42000.0
