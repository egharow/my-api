import io
import json
from datetime import date, datetime, timedelta

import pytest

from ftracker import accounts, balances, fx
from ftracker.parsers.base import ParsedTxn
from .helpers import card_file, run_import


class FakeNet:
    """Stands in for the internet: maps a URL fragment to a response body, or raises when offline."""
    def __init__(self, responses=None, offline=False):
        self.responses, self.offline, self.calls = responses or {}, offline, []

    def __call__(self, req, timeout=None):
        self.calls.append(req.full_url)
        if self.offline:
            raise OSError("no network")
        for frag, body in self.responses.items():
            if frag in req.full_url:
                return io.BytesIO(body.encode())
        raise OSError("blocked")


FRANKFURTER = json.dumps({"amount": 1.0, "base": "USD", "date": "2026-10-05", "rates": {"ILS": 3.612}})
ER_API = json.dumps({"result": "success", "time_last_update_utc": "Mon, 05 Oct 2026 00:02:31 +0000", "rates": {"ILS": 3.6}})


def test_historical_lookup_uses_latest_earlier_day_inverse_and_prefers_manual(conn):
    fx.set_rate(conn, "2026-09-01", "USD", "ILS", 3.6, "boi")
    fx.set_rate(conn, "2026-09-10", "USD", "ILS", 3.7, "boi")
    assert fx.rate_on(conn, "2026-09-05", "USD", "ILS") == 3.6
    assert abs(fx.rate_on(conn, "2026-09-12", "ILS", "USD") - 1 / 3.7) < 1e-9
    assert fx.rate_on(conn, "2026-08-01", "USD", "ILS") is None
    fx.set_rate(conn, "2026-09-10", "USD", "ILS", 3.65, "manual")
    fx.set_rate(conn, "2026-09-10", "USD", "ILS", 3.99, "web")           # worse source must not win
    assert fx.rate_on(conn, "2026-09-10", "USD", "ILS") == 3.65
    with pytest.raises(ValueError):
        fx.set_rate(conn, "2026-09-10", "USD", "ILS", 0)


def test_dollar_assets_use_the_current_rate_on_every_date(conn):
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
    assert balances.net_worth(conn, "2026-06-15")["total"] == 400.0          # the $100 is valued at 4.0, not the 3.0 of that June
    assert balances.net_worth(conn, "2026-10-05")["total"] == 1000 + 400 - 200
    assert round(balances.net_worth(conn, "2026-10-05", "USD")["total"], 2) == 300.0
    fx.set_mode(conn, "historical")                                          # the optional alternative
    assert balances.net_worth(conn, "2026-06-15")["total"] == 300.0
    with pytest.raises(ValueError):
        fx.set_mode(conn, "sometimes")


def test_no_rate_at_all_says_what_to_do_and_never_guesses(conn):
    with pytest.raises(LookupError) as exc:
        fx.convert_asset(conn, 10, "USD", "ILS")
    assert "Enter today's rate" in str(exc.value)
    assert fx.convert_asset(conn, 10, "ILS", "ILS") == 10


def test_expenses_are_never_converted_and_create_no_rates(home, conn, monkeypatch):
    t = ParsedTxn(date(2026, 9, 28), "SERVICE", -61.55, "ILS", orig_amount=-20.0, orig_currency="USD", voucher="q")
    run_import(home, conn, monkeypatch, [("c.xlsx", card_file(txns=[t]))])
    row = conn.execute("SELECT amount, currency, orig_amount, orig_currency FROM transactions").fetchone()
    assert (row["amount"], row["currency"]) == (-61.55, "ILS")               # the shekel charge is what counts
    assert (row["orig_amount"], row["orig_currency"]) == (-20.0, "USD")      # the dollar amount is kept for reference
    assert conn.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0] == 0


def test_refresh_fetches_when_stale_skips_when_fresh_and_survives_offline(conn):
    net = FakeNet({"frankfurter": FRANKFURTER})
    res = fx.refresh_current(conn, "2026-10-05", net)
    assert (res["status"], res["rate"], res["date"], res["source"]) == ("fetched", 3.612, "2026-10-05", "frankfurter")
    assert fx.current_rate(conn)["rate"] == 3.612
    net.calls.clear()
    assert fx.refresh_current(conn, "2026-10-05", net)["status"] == "fresh" and not net.calls
    stale = fx.refresh_current(conn, "2026-10-09", FakeNet({"er-api": ER_API}))       # first source blocked, second answers
    assert (stale["status"], stale["rate"], stale["date"]) == ("fetched", 3.6, "2026-10-05")
    offline = fx.refresh_current(conn, "2026-12-01", FakeNet(offline=True))
    assert offline["status"] == "failed" and offline["rate"] == 3.6                  # keeps using the last rate it had


def test_a_rate_you_typed_wins_over_a_fetched_one_for_the_same_day(conn):
    fx.set_rate(conn, "2026-10-05", "USD", "ILS", 3.7, "manual")
    fx.refresh_current(conn, "2026-10-05", FakeNet({"frankfurter": FRANKFURTER}), force=True)
    assert fx.current_rate(conn)["rate"] == 3.7


def test_automatic_refresh_is_rate_limited(conn):
    net = FakeNet(offline=True)
    assert fx.ensure_fresh(conn, "2026-10-05", net)["status"] == "failed"
    assert fx.ensure_fresh(conn, "2026-10-05", net) is None                          # not again for a few hours
    conn.execute("UPDATE settings SET value = ? WHERE key = 'fx_last_attempt'",
                 ((datetime.now() - timedelta(hours=7)).isoformat(),))
    first = len(net.calls)
    assert fx.ensure_fresh(conn, "2026-10-05", net)["status"] == "failed" and len(net.calls) == 2 * first


def test_unexpected_responses_are_rejected_not_trusted():
    for body in ('{"rates": {"EUR": 0.9}}', "<html>login</html>", '{"rates": {"ILS": 0}}', '{"rates": {"ILS": -1}}'):
        with pytest.raises(Exception):
            fx.parse_web_rate(body)


def test_balances_due_monthly_and_stale_accounts(conn):
    accounts.add_account(conn, "pension", "x", "Pension")
    assert balances.balances_due(conn, "2026-10-05") == (True, None)
    balances.set_balance(conn, "Pension", 5, "2026-10-01")
    assert balances.balances_due(conn, "2026-10-20")[0] is False
    assert balances.balances_due(conn, "2026-11-10")[0] is True
    assert [r["label"] for r in balances.stale(conn, "2026-11-10")] == ["Pension"]
    assert [p["current"] for p in balances.pending(conn, "2026-11-10")] == [False]


def test_monthly_checklist_only_ticks_balances_updated_this_month(conn):
    from ftracker import expected
    accounts.add_account(conn, "pension", "x", "Pension")
    balances.set_balance(conn, "Pension", 5, "2026-10-09")
    p = balances.pending(conn, "2026-11-03")[0]
    assert p["current"] is True and p["this_month"] is False      # recent, but not this month
    item = [i for g in expected.checklist(conn, "2026-11-03")["groups"] for i in g["items"] if i["label"] == "Balances (monthly)"][0]
    assert item["status"] == "todo" and item["children"][0]["status"] == "todo"
    balances.set_balance(conn, "Pension", 6, "2026-11-02")
    item = [i for g in expected.checklist(conn, "2026-11-03")["groups"] for i in g["items"] if i["label"] == "Balances (monthly)"][0]
    assert item["status"] == "done" and item["children"][0]["status"] == "done"


def test_fetching_automatically_replaces_a_rate_you_typed_earlier(conn):
    fx.set_rate(conn, "2026-10-05", "USD", "ILS", 3.13, "manual")           # typed once, and it kept winning over fresh rates
    conn.commit()
    assert fx.current_rate(conn)["rate"] == 3.13
    res = fx.refresh_current(conn, "2026-10-05", FakeNet({"frankfurter": FRANKFURTER}), force=True, replace_typed=True)
    assert res["status"] == "fetched" and fx.current_rate(conn)["rate"] == 3.612 and fx.current_rate(conn)["source"] == "web"
    # a normal refresh does not undo a rate you type after that
    fx.set_rate(conn, "2026-10-05", "USD", "ILS", 3.2, "manual")
    conn.commit()
    assert fx.refresh_current(conn, "2026-10-05", FakeNet({"frankfurter": FRANKFURTER}))["status"] == "fresh"
    assert fx.current_rate(conn)["rate"] == 3.2


BOI_ONE = json.dumps({"key": "USD", "currentExchangeRate": 3.2, "currentChange": 0.31, "unit": 1, "lastUpdate": "2026-10-05T15:31:00"})
BOI_ALL = json.dumps({"exchangeRates": [{"key": "EUR", "currentExchangeRate": 3.7, "unit": 1, "lastUpdate": "2026-10-05T15:31:00"},
                                        {"key": "USD", "currentExchangeRate": 3.2, "unit": 1, "lastUpdate": "2026-10-05T15:31:00"}]})


def test_the_bank_of_israel_rate_is_read_in_either_of_its_layouts():
    assert fx.parse_boi_json(BOI_ONE) == ("2026-10-05", 3.2)
    assert fx.parse_boi_json(BOI_ALL) == ("2026-10-05", 3.2)
    with pytest.raises(ValueError):
        fx.parse_boi_json(json.dumps({"key": "USD", "currentExchangeRate": 99, "unit": 1}))      # an absurd number is not trusted
    with pytest.raises(ValueError):
        fx.parse_boi_json(json.dumps({"exchangeRates": [{"key": "EUR", "currentExchangeRate": 3.7}]}))


def test_the_bank_of_israel_rate_wins_over_the_free_web_rates(conn):
    fx.set_rate(conn, "2026-10-05", "USD", "ILS", 3.13, "web")               # a rate fetched earlier from a free source
    conn.commit()
    res = fx.refresh_current(conn, "2026-10-05", FakeNet({"boi.org.il": BOI_ONE, "frankfurter": FRANKFURTER}), force=True)
    cur = fx.current_rate(conn)
    assert res["source"] == "boi" and cur["rate"] == 3.2 and cur["source"] == "boi"
    assert conn.execute("SELECT COUNT(*) FROM fx_rates WHERE source = 'web'").fetchone()[0] == 0


def test_the_web_rates_are_the_fallback_when_the_bank_of_israel_does_not_answer(conn):
    res = fx.refresh_current(conn, "2026-10-05", FakeNet({"frankfurter": FRANKFURTER}), force=True)
    assert res["status"] == "fetched" and res["source"] == "frankfurter"


def test_the_bank_of_israel_data_service_is_the_second_try(conn):
    csv_body = "TIME_PERIOD,OBS_VALUE\n2026-10-02,3.19\n2026-10-05,3.2\n"
    res = fx.refresh_current(conn, "2026-10-05", FakeNet({"edge.boi.gov.il": csv_body}), force=True)
    assert res["source"] == "boi" and res["rate"] == 3.2 and res["date"] == "2026-10-05"
