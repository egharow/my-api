from ftracker import accounts, balances, fx
from .helpers import card_file
from ftracker.parsers.base import ParsedTxn
from datetime import date


def test_rate_lookup_uses_latest_earlier_day_inverse_and_prefers_manual(conn):
    fx.set_rate(conn, "2026-09-01", "USD", "ILS", 3.6, "boi")
    fx.set_rate(conn, "2026-09-10", "USD", "ILS", 3.7, "boi")
    assert fx.rate_on(conn, "2026-09-05", "USD", "ILS") == 3.6
    assert fx.rate_on(conn, "2026-09-12", "USD", "ILS") == 3.7
    assert abs(fx.rate_on(conn, "2026-09-12", "ILS", "USD") - 1 / 3.7) < 1e-9
    assert fx.rate_on(conn, "2026-08-01", "USD", "ILS") is None
    fx.set_rate(conn, "2026-09-10", "USD", "ILS", 3.65, "manual")
    fx.set_rate(conn, "2026-09-10", "USD", "ILS", 3.99, "card")      # worse source must not win
    assert fx.rate_on(conn, "2026-09-10", "USD", "ILS") == 3.65


def test_convert_without_a_rate_says_what_to_do(conn):
    try:
        fx.convert(conn, 10, "USD", "ILS", "2026-01-01")
    except LookupError as exc:
        assert "fx set" in str(exc)
    else:
        raise AssertionError("expected LookupError")


def test_card_charge_in_dollars_records_its_implied_rate(home, conn, monkeypatch):
    from .helpers import run_import
    t = ParsedTxn(date(2026, 9, 28), "SERVICE", -61.55, "ILS", orig_amount=-20.0, orig_currency="USD", voucher="q")
    run_import(home, conn, monkeypatch, [("c.xlsx", card_file(txns=[t]))])
    assert abs(fx.rate_on(conn, "2026-09-28", "USD", "ILS") - 3.0775) < 1e-6


def test_net_worth_converts_both_currencies_at_the_date_and_debts_subtract(conn):
    accounts.add_owner(conn, "Ely")
    accounts.add_account(conn, "bank", "x", "Current", "Ely")
    accounts.add_account(conn, "investment", "x", "Brokerage USD", "Ely", currency="USD")
    accounts.add_account(conn, "loan", "x", "Car loan", "Ely")
    fx.set_rate(conn, "2026-06-01", "USD", "ILS", 3.0, "manual")
    fx.set_rate(conn, "2026-10-01", "USD", "ILS", 4.0, "manual")
    balances.set_balance(conn, "Current", 1000, "2026-10-01")
    balances.set_balance(conn, "Brokerage USD", 100, "2026-06-01")
    balances.set_balance(conn, "Brokerage USD", 100, "2026-10-01")
    balances.set_balance(conn, "Car loan", 200, "2026-10-01")               # entered positive, stored as a debt
    assert balances.net_worth(conn, "2026-06-15")["total"] == 300.0         # old rate for old date
    assert balances.net_worth(conn, "2026-10-05")["total"] == 1000 + 400 - 200
    assert round(balances.net_worth(conn, "2026-10-05", "USD")["total"], 2) == 300.0


def test_balances_due_every_two_months_and_stale_accounts(conn):
    accounts.add_account(conn, "pension", "x", "Pension")
    assert balances.balances_due(conn, "2026-10-05") == (True, None)
    balances.set_balance(conn, "Pension", 5, "2026-08-20")
    assert balances.balances_due(conn, "2026-10-05")[0] is False
    assert balances.balances_due(conn, "2026-11-05")[0] is True
    assert [r["label"] for r in balances.stale(conn, "2026-11-05")] == ["Pension"]
