import pytest

from ftracker import accounts, balances, fx, goals


@pytest.fixture
def world(conn):
    accounts.add_owner(conn, "Ely"); accounts.add_owner(conn, "Shir")
    goals.set_birth_date(conn, "Ely", "1990-06-15")
    accounts.add_account(conn, "savings", "x", "Savings", "Ely")
    accounts.add_account(conn, "investment", "x", "Brokerage", "Ely", currency="USD")
    balances.set_balance(conn, "Savings", 100_000, "2026-04-01")
    balances.set_balance(conn, "Savings", 160_000, "2026-10-01")
    return conn


def test_behind_when_the_pace_is_short_of_what_is_needed(world):
    gid = goals.add_goal(world, "Deposit", 400_000, target_date="2027-10-01", accounts_=["Savings"])
    p = goals.progress(world, gid, "2026-10-05")
    assert p["status"] == "behind" and p["current"] == 160_000
    assert abs(p["growth_per_month"] - 10_000) < 1
    assert 19_000 < p["required_per_month"] < 21_500           # about 240k over a bit under 12 months
    assert p["gap_per_month"] > 9_000
    assert p["projected_date"].startswith("2028-10")            # 24 months at 10k a month


def test_extra_saving_can_make_a_goal_on_track(world):
    gid = goals.add_goal(world, "Deposit", 400_000, target_date="2027-10-01", accounts_=["Savings"])
    assert goals.progress(world, gid, "2026-10-05", extra_monthly=11_000)["status"] == "on_track"


def test_age_goal_uses_the_owners_birth_date(world):
    gid = goals.add_goal(world, "By 40", 1_000_000, target_age=40, owner="Ely", accounts_=["Savings"])
    assert goals.progress(world, gid, "2026-10-05")["target_date"] == "2030-06-15"
    with pytest.raises(ValueError):
        goals.add_goal(world, "x", 1, target_age=40, owner="Shir")      # Shir has no birth date yet
    with pytest.raises(ValueError):
        goals.add_goal(world, "x", 1, target_age=40)                    # whose age?


def test_reached_goal_and_validation(world):
    gid = goals.add_goal(world, "Small", 50_000, accounts_=["Savings"])
    assert goals.progress(world, gid, "2026-10-05")["status"] == "reached"
    with pytest.raises(ValueError):
        goals.add_goal(world, "x", 0)
    with pytest.raises(ValueError):
        goals.add_goal(world, "x", 10, target_date="2030-01-01", target_age=40, owner="Ely")


def test_goal_without_a_date_only_tracks(world):
    gid = goals.add_goal(world, "Someday", 400_000, accounts_=["Savings"])
    p = goals.progress(world, gid, "2026-10-05")
    assert p["status"] == "tracking" and p["required_per_month"] is None and p["projected_date"]


def test_dollar_accounts_need_a_rate_and_never_guess(world):
    balances.set_balance(world, "Brokerage", 10_000, "2026-10-01")
    gid = goals.add_goal(world, "Mixed", 400_000, target_date="2027-10-01", accounts_=["Savings", "Brokerage"])
    assert goals.progress(world, gid, "2026-10-05")["status"] == "needs_fx"
    fx.set_rate(world, "2026-10-01", "USD", "ILS", 3.5, "manual")
    assert goals.progress(world, gid, "2026-10-05")["current"] == 160_000 + 35_000


def test_no_linked_accounts_means_net_worth_scoped_to_the_owner(world):
    accounts.add_account(world, "savings", "x", "Shir savings", "Shir")
    balances.set_balance(world, "Shir savings", 5_000, "2026-10-01")
    household = goals.add_goal(world, "Household", 1_000_000)
    elys = goals.add_goal(world, "Ely only", 1_000_000, owner="Ely")
    assert goals.progress(world, household, "2026-10-05")["current"] == 165_000
    assert goals.progress(world, elys, "2026-10-05")["current"] == 160_000


def test_required_monthly_with_and_without_growth():
    assert goals.required_monthly(0, 12_000, 12, 0) == 1000
    assert goals.required_monthly(0, 12_000, 12, 0.10) < 1000
    assert goals.required_monthly(20_000, 12_000, 12, 0) == 0
    assert goals.months_to_reach(0, 1000, 100) == 10 and goals.months_to_reach(0, 1000, 0) is None
