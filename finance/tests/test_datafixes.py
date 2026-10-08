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
