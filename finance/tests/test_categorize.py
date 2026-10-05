from datetime import date

import pytest

from ftracker import accounts, categorize
from ftracker.parsers.base import ParsedTxn
from .helpers import card_file, run_import


def _import(home, conn, monkeypatch, txns):
    run_import(home, conn, monkeypatch, [("c.xlsx", card_file(txns=txns))])
    return conn.execute("SELECT t.*, c.name AS cat FROM transactions t JOIN categories c ON c.id = t.category_id "
                        "ORDER BY t.id").fetchall()


def _t(desc, amt, v):
    return ParsedTxn(txn_date=date(2026, 9, 3), description=desc, amount=-amt, currency="ILS", voucher=v)


def test_builtin_rule_is_only_a_proposal(home, conn, monkeypatch):
    rows = _import(home, conn, monkeypatch, [_t("סופר שפע סיטי", 40, "1")])
    assert rows[0]["cat"] == "Groceries" and rows[0]["category_status"] == "proposed"


def test_bit_is_a_neutral_transfer_and_matches_whole_word_only(home, conn, monkeypatch):
    rows = _import(home, conn, monkeypatch, [_t("העברה ב BIT בנה\"פ", 750, "1"), _t("HABIT SHOP", 20, "2")])
    assert (rows[0]["cat"], rows[0]["kind"], rows[0]["category_status"]) == ("Transfer to household", "transfer", "approved")
    assert rows[1]["cat"] == "Uncategorised"


def test_unknown_merchant_goes_to_review(home, conn, monkeypatch):
    rows = _import(home, conn, monkeypatch, [_t("TOTALLY NEW PLACE", 30, "1")])
    assert rows[0]["cat"] == "Uncategorised" and rows[0]["category_status"] == "proposed"


def test_approval_with_learning_applies_next_time_and_user_rule_wins(home, conn, monkeypatch):
    rows = _import(home, conn, monkeypatch, [_t("CORNER STORE 12", 30, "1")])
    categorize.approve(conn, [rows[0]["id"]], "Groceries", learn=True)
    again = categorize.find_rule(conn, "CORNER STORE 12", -10, "card")
    assert again["source"] == "learned"
    categorize.add_user_rule(conn, "CORNER STORE", "Fun")
    assert categorize.find_rule(conn, "CORNER STORE 12", -10, "card")["source"] == "user"


def test_similar_past_payment_is_suggested(home, conn, monkeypatch):
    rows = _import(home, conn, monkeypatch, [_t("NEIGHBOURHOOD DELI 3", 30, "1")])
    categorize.approve(conn, [rows[0]["id"]], "Eating out")
    run_import(home, conn, monkeypatch, [("d.xlsx", card_file(
        billing=date(2026, 11, 2), txns=[_t("NEIGHBOURHOOD DELI 7", 25, "9")]))])
    new = conn.execute("SELECT t.proposal_basis, c.name FROM transactions t JOIN categories c ON c.id = t.category_id "
                       "WHERE voucher = '9'").fetchone()
    assert new["name"] == "Eating out" and "similar" in new["proposal_basis"]


def test_refund_is_recognised(home, conn, monkeypatch):
    rows = _import(home, conn, monkeypatch, [ParsedTxn(date(2026, 9, 3), "SOME STORE", 40.0, "ILS", voucher="1")])
    assert rows[0]["kind"] == "refund"


def test_unknown_category_rejected(home, conn):
    with pytest.raises(ValueError):
        categorize.add_user_rule(conn, "X", "No such category")
