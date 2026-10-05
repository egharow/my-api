from datetime import date

import pytest

from ftracker import accounts, commits, expected, importer, reconcile
from ftracker import discrepancies as dx
from ftracker.parsers.base import ParsedTxn
from .helpers import bank_file, card_file, card_txn, run_import


def _items(conn, type_=None):
    sql = "SELECT * FROM discrepancies WHERE status = 'open'" + (" AND type = ?" if type_ else "")
    return conn.execute(sql, (type_,) if type_ else ()).fetchall()


def test_card_payment_reconciles_to_statement_and_is_not_double_counted(home, conn, monkeypatch):
    card = card_file(last4="1111", billing=date(2026, 10, 2))                      # total 150.00
    bank = bank_file([(date(2026, 10, 2), "ל.מאסטרקרד", -150.0)])
    run_import(home, conn, monkeypatch, [("card.xlsx", card), ("bank.pdf", bank)])
    s = conn.execute("SELECT * FROM statements WHERE billing_date IS NOT NULL").fetchone()
    assert s["matched_bank_txn_id"] is not None
    assert not _items(conn, "missing_statement") and not _items(conn, "payment_mismatch")
    from ftracker import summary
    spent = sum(r["spent"] for r in summary.spending_by_category(conn, "2026-10"))
    assert spent == 150.0                       # the card lines, not the 150.00 payment as well


def test_bank_payment_without_statement_is_flagged_then_cleared_when_it_arrives(home, conn, monkeypatch):
    bank = bank_file([(date(2026, 9, 2), "ל.מאסטרקרד", -150.0)])
    run_import(home, conn, monkeypatch, [("bank.pdf", bank)])
    assert len(_items(conn, "missing_statement")) == 1
    card = card_file(billing=date(2026, 9, 2))
    run_import(home, conn, monkeypatch, [("card.xlsx", card)])
    assert not _items(conn, "missing_statement")
    assert dx.list_items(conn, ("resolved",))[0]["auto_resolved"] == 1


def test_amount_mismatch_between_bank_and_statement_is_named(home, conn, monkeypatch):
    card = card_file(billing=date(2026, 10, 2))                                    # 150.00
    bank = bank_file([(date(2026, 10, 2), "ל.מאסטרקרד", -160.0)])
    run_import(home, conn, monkeypatch, [("card.xlsx", card), ("bank.pdf", bank)])
    item = _items(conn, "payment_mismatch")[0]
    assert "10.00" in item["detail"]


def test_statement_whose_lines_do_not_add_up_is_flagged(home, conn, monkeypatch):
    run_import(home, conn, monkeypatch, [("card.xlsx", card_file(total=999.0))])
    assert len(_items(conn, "statement_total_mismatch")) == 1


def test_same_file_twice_is_a_duplicate_and_overlap_adds_only_new_lines(home, conn, monkeypatch):
    rows = [(date(2026, 8, 1), "SALARY", 1000.0), (date(2026, 8, 5), "RENT", -400.0)]
    run_import(home, conn, monkeypatch, [("a.pdf", bank_file(rows, last4="9999"))])
    more = rows + [(date(2026, 8, 9), "SHOP", -50.0)]
    report = run_import(home, conn, monkeypatch, [("b.pdf", bank_file(more, last4="9999"))])
    f = report.files[0]
    assert (f.new_txns, f.skipped_txns) == (1, 2)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 3
    # identical bytes -> set aside as a duplicate
    (home.inbox / "a-again.pdf").write_bytes((home.archive / "bank/leumi/account-9999").glob("*.pdf").__next__().read_bytes())
    again = importer.import_inbox(home, conn, date(2026, 10, 5))
    assert again.files[0].status == "duplicate"
    assert (home.duplicates / "a-again.pdf").exists()


def test_unrecognised_file_is_set_aside_with_a_reason(home, conn):
    (home.inbox / "notes.txt").write_text("hello")
    (home.inbox / "weird.xlsx").write_bytes(b"not really excel")
    report = importer.import_inbox(home, conn, date(2026, 10, 5))
    assert [f.status for f in report.files] == ["unrecognised", "unrecognised"]
    assert (home.unrecognised / "weird.xlsx").exists() and (home.unrecognised / "weird.xlsx.txt").exists()
    assert report.batch_id is None


def test_archive_layout_by_type_and_card(home, conn, monkeypatch):
    accounts.add_owner(conn, "Shir", ["שיר"])
    shared = card_file(issuer="amex", last4="2222")
    shared.statements.append(card_file(issuer="amex", last4="3333").statements[0])
    run_import(home, conn, monkeypatch, [
        ("one.xlsx", card_file(issuer="isracard", last4="1111", holder="על שם שיר")),
        ("two.xls", shared),
        ("bank.pdf", bank_file([(date(2026, 8, 1), "SALARY", 1.0)], last4="4567")),
    ])
    found = sorted(str(p.relative_to(home.archive)).replace("\\", "/") for p in home.archive.rglob("*") if p.is_file())
    assert found == [
        "bank/leumi/account-4567/2026-07-04_to_2026-10-04_leumi-4567.pdf",
        "credit-cards/amex/2026-10_amex-2222+3333.xls",
        "credit-cards/isracard/1111-shir/2026-10_isracard-1111.xlsx",
    ]
    assert not list(home.inbox.glob("*.xlsx"))      # filed away, inbox is empty again


def test_holder_alias_sets_owner_but_name_is_not_stored(home, conn, monkeypatch):
    accounts.add_owner(conn, "Shir", ["שיר"])
    run_import(home, conn, monkeypatch, [("one.xlsx", card_file(holder="על שם שיר דוגמה"))])
    owner = conn.execute("SELECT o.name FROM accounts a JOIN owners o ON o.id = a.owner_id").fetchone()[0]
    assert owner == "Shir"
    dump = " ".join(str(tuple(r)) for t in ("transactions", "statements", "accounts", "source_files")
                    for r in conn.execute(f"SELECT * FROM {t}"))
    assert "דוגמה" not in dump


def test_fee_refund_pending_overdue_and_refunded(home, conn, monkeypatch):
    fee = (date(2026, 10, 1), "עמל.ערוץ יש 10", -16.5)
    run_import(home, conn, monkeypatch, [("b.pdf", bank_file([fee], end=date(2026, 10, 4)))], today=date(2026, 10, 5))
    assert reconcile.fee_status(conn, "2026-10-05")[0]["state"] == "pending"
    assert not _items(conn, "fee_unrefunded")
    reconcile.track_fee_refunds(conn, "2026-10-20")
    assert reconcile.fee_status(conn, "2026-10-20")[0]["state"] == "overdue"
    assert len(_items(conn, "fee_unrefunded")) == 1
    refund = (date(2026, 10, 22), "ריבית ידני*", 16.5)
    run_import(home, conn, monkeypatch, [("b2.pdf", bank_file([fee, refund], end=date(2026, 10, 25)))],
               today=date(2026, 10, 25))
    assert reconcile.fee_status(conn, "2026-10-25")[0]["state"] == "refunded"
    assert not _items(conn, "fee_unrefunded")


def test_overdraft_letter_fee_does_not_expect_a_refund(home, conn, monkeypatch):
    run_import(home, conn, monkeypatch, [("b.pdf", bank_file([(date(2026, 9, 25), "מכתב חריגה עוש", -5.0)]))])
    assert reconcile.fee_status(conn, "2026-12-01") == []


def test_balance_chain_accepts_value_date_ordering_and_catches_a_missing_line(home, conn, monkeypatch):
    rows = [(date(2026, 9, 30), "SALARY", 500.0), (date(2026, 10, 1), "SHOP", -20.0),
            (date(2026, 9, 30), "INTEREST", -4.94)]       # dated earlier but processed later
    run_import(home, conn, monkeypatch, [("ok.pdf", bank_file(rows))])
    assert not _items(conn, "balance_chain")
    broken = bank_file([(date(2026, 9, 1), "A", 100.0), (date(2026, 9, 2), "B", -10.0), (date(2026, 9, 3), "C", -20.0)],
                       last4="8888")
    broken.statements[0].txns.pop(1)                       # drop a line from the middle
    run_import(home, conn, monkeypatch, [("broken.pdf", broken)])
    assert len(_items(conn, "balance_chain")) == 1


def test_large_item_and_untargeted_transfer_are_flagged_and_cleared(home, conn, monkeypatch):
    big = card_file(txns=[card_txn(date(2026, 9, 9), "ONE OFF SERVICE", 3500.0, "x1")])
    bank = bank_file([(date(2026, 9, 2), "העברה דיגיטל", -10000.0)])
    run_import(home, conn, monkeypatch, [("c.xlsx", big), ("b.pdf", bank)])
    assert len(_items(conn, "large_item")) == 1 and len(_items(conn, "transfer_destination")) == 1
    accounts.add_account(conn, "savings", "manual", "Savings", "Ely" if False else None)
    res = accounts.set_destination_rule(conn, "העברה דיגיטל", "Savings")
    assert res["updated"] == 1
    reconcile.run(conn, 1, "2026-10-05")
    assert not _items(conn, "transfer_destination")


def test_submit_records_time_requires_acknowledgement_and_locks_numbers(home, conn, monkeypatch):
    from ftracker import categorize
    run_import(home, conn, monkeypatch, [("c.xlsx", card_file())])
    with pytest.raises(commits.NeedsAcknowledgement):
        commits.submit(conn, home, 1, today="2026-10-05")
    cid = commits.submit(conn, home, 1, acknowledge=True, today="2026-10-05")
    row = conn.execute("SELECT * FROM commits WHERE id = ?", (cid,)).fetchone()
    assert row["version"] == 1 and row["committed_at"] and (home.root / row["backup_path"]).exists()
    assert "proposed category" in row["summary_json"]          # open issues are recorded
    tid = conn.execute("SELECT id FROM transactions").fetchone()[0]
    with pytest.raises(PermissionError):
        categorize.approve(conn, [tid], "Fun")
    with pytest.raises(ValueError):
        commits.reopen(conn, 1, "  ")
    commits.reopen(conn, 1, "wrong category")
    categorize.approve(conn, [tid], "Fun")
    commits.submit(conn, home, 1, acknowledge=True, today="2026-10-05")
    versions = conn.execute("SELECT version, reason FROM commits ORDER BY version").fetchall()
    assert [(v["version"], v["reason"]) for v in versions] == [(1, None), (2, "wrong category")]


def test_comments_persist_have_authors_and_can_close_an_item(home, conn, monkeypatch):
    run_import(home, conn, monkeypatch, [("c.xlsx", card_file(txns=[card_txn(date(2026, 9, 9), "ONE OFF", 2000.0, "z")]))])
    item = _items(conn, "large_item")[0]["id"]
    c1 = dx.add_comment(conn, item, "Ely", "annual payment")
    dx.set_status(conn, item, "explained", "Ely", follow_up_date="2026-12-01")
    with pytest.raises(PermissionError):
        dx.edit_comment(conn, c1, "Shir", "hijack")
    dx.edit_comment(conn, c1, "Ely", "annual payment, yearly in September")
    assert [c["body"] for c in dx.thread(conn, item)] == ["annual payment, yearly in September"]
    assert not any(d["id"] == item for d in dx.list_items(conn, ("open",)))
    assert any(d["id"] == item for d in dx.list_items(conn, ("open", "explained"), today="2026-12-02"))


def test_expected_files_missing_card_month_and_stale_bank(home, conn, monkeypatch):
    for billing in (date(2026, 7, 2), date(2026, 9, 2)):                              # August is missing
        run_import(home, conn, monkeypatch, [(f"c{billing}.xlsx", card_file(
            billing=billing, txns=[card_txn(billing, "S", 10.0, f"v{billing}")]))], today=date(2026, 10, 5))
    run_import(home, conn, monkeypatch, [("b.pdf", bank_file([(date(2026, 8, 1), "X", 1.0)], end=date(2026, 8, 30)))])
    items = expected.expected_files(conn, "2026-10-05")
    kinds = {(i["source"], i["period"], i["status"]) for i in items}
    assert ("Isracard 1111", "2026-08", "missing") in kinds
    assert ("Isracard 1111", "2026-10", "due") in kinds          # billed on the 2nd: due, not yet late
    later = {(i["source"], i["period"], i["status"]) for i in expected.expected_files(conn, "2026-10-09")}
    assert ("Isracard 1111", "2026-10", "missing") in later      # more than 3 days on, it is missing
    assert any(i["source"] == "Leumi 9999" and i["status"] == "due" for i in items)
    assert any(i["source"] == "Balances" for i in items)
