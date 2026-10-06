import json
from ftracker import db
from datetime import date
from urllib.parse import urlencode

import pytest

from ftracker import accounts, categorize, commits, firstrun, importer, sheet_import
from ftracker.web import App
from .helpers import card_file, card_txn, run_import
from .test_sheet_import import _workbook

H = {"host": "localhost:8765"}


@pytest.fixture
def loaded(conn, tmp_path):
    accounts.add_owner(conn, "Ely"); accounts.add_owner(conn, "Shir")
    data = sheet_import.parse_workbook(_workbook(tmp_path / "h.xlsx"))
    return conn, data


def _row(conn, desc):
    return conn.execute("SELECT t.kind, c.name AS cat, t.category_status FROM transactions t JOIN categories c ON c.id = t.category_id "
                        "WHERE t.description = ?", (desc,)).fetchone()


def test_your_own_category_on_a_bit_line_is_kept_and_only_blank_ones_become_transfers(loaded):
    conn, data = loaded
    blank = next(l for m in data.months for l in m.log if l.description == "MYSTERY")
    blank.description = "בנהפ BIT העברה ב 2"            # a Bit line you left uncategorised
    res = sheet_import.save(conn, data)
    assert tuple(_row(conn, "בנהפ BIT העברה ב")) == ("purchase", "Eating out", "approved")          # you set it: kept
    assert tuple(_row(conn, "בנהפ BIT העברה ב 2")) == ("transfer", "Transfer to household", "approved")
    assert res["transfers_reclassified"] == 1


def test_investment_in_your_sheet_means_saving_not_spending(loaded):
    conn, data = loaded
    data.months[0].log[0].category = "Investment"
    sheet_import.save(conn, data)
    name = conn.execute("SELECT c.name, c.neutral FROM transactions t JOIN categories c ON c.id = t.category_id WHERE t.description = ?",
                        (data.months[0].log[0].description,)).fetchone()
    assert tuple(name) == ("Transfer to savings", 1)


def test_restore_puts_your_categories_back_where_an_earlier_version_overrode_them(loaded):
    conn, data = loaded
    sheet_import.save(conn, data)
    tid = conn.execute("SELECT id FROM transactions WHERE description = 'בנהפ BIT העברה ב'").fetchone()[0]
    transfer = conn.execute("SELECT id FROM categories WHERE name = 'Transfer to household'").fetchone()[0]
    conn.execute("UPDATE transactions SET category_id = ?, kind = 'transfer', proposal_basis = 'builtin rule' WHERE id = ?", (transfer, tid))
    assert sheet_import.restore_categories(conn, data) == 1
    assert tuple(_row(conn, "בנהפ BIT העברה ב")) == ("purchase", "Eating out", "approved")
    assert sheet_import.restore_categories(conn, data) == 0                                       # nothing left to fix


def test_restore_never_touches_what_you_changed_yourself_or_submitted(loaded, home):
    conn, data = loaded
    sheet_import.save(conn, data)
    categorize.approve(conn, [conn.execute("SELECT id FROM transactions WHERE description = 'SUPER EXAMPLE 12'").fetchone()[0]], "Fun")
    assert sheet_import.restore_categories(conn, data) == 0
    assert _row(conn, "SUPER EXAMPLE 12")["cat"] == "Fun"                                         # your later choice stands
    conn.execute("UPDATE batches SET status = 'committed'")
    tid = conn.execute("SELECT id FROM transactions WHERE description = 'STORE REFUND'").fetchone()[0]
    conn.execute("UPDATE transactions SET category_id = (SELECT id FROM categories WHERE name = 'Fun'), proposal_basis = 'x' WHERE id = ?", (tid,))
    assert sheet_import.restore_categories(conn, data) == 0


def test_the_startup_repair_runs_once(conn, home, tmp_path, monkeypatch):
    appdir = tmp_path / "ad"; (appdir / "seed").mkdir(parents=True)
    data = sheet_import.parse_workbook(_workbook(appdir / "seed" / "history.xlsx"))
    accounts.add_owner(conn, "Ely"); accounts.add_owner(conn, "Shir")
    sheet_import.save(conn, data)
    tid = conn.execute("SELECT id FROM transactions WHERE description = 'בנהפ BIT העברה ב'").fetchone()[0]
    conn.execute("UPDATE transactions SET category_id = (SELECT id FROM categories WHERE name = 'Transfer to household'), kind = 'transfer', "
                 "proposal_basis = 'builtin rule' WHERE id = ?", (tid,))
    assert firstrun.repair(conn, appdir) == 1
    assert firstrun.repair(conn, appdir) == 0
    assert _row(conn, "בנהפ BIT העברה ב")["cat"] == "Eating out"


# ---- merging and renaming categories ---------------------------------------------------------------

def test_merging_moves_lines_and_rules_and_removes_a_category_you_created(loaded):
    conn, data = loaded
    sheet_import.save(conn, data)                       # creates the stray category "Vituri"
    before = conn.execute("SELECT COUNT(*) FROM transactions t JOIN categories c ON c.id = t.category_id WHERE c.name = 'Vituri'").fetchone()[0]
    assert before == 1
    res = categorize.merge_category(conn, "Vituri", "Medical")
    assert res["lines"] == 1 and res["removed"]
    assert _row(conn, "NEW CATEGORY")["cat"] == "Medical"
    assert conn.execute("SELECT COUNT(*) FROM categories WHERE name = 'Vituri'").fetchone()[0] == 0


def test_merging_a_built_in_category_keeps_it_available(loaded):
    conn, data = loaded
    sheet_import.save(conn, data)
    res = categorize.merge_category(conn, "Gifts", "Fun")
    assert not res["removed"] and conn.execute("SELECT COUNT(*) FROM categories WHERE name = 'Gifts'").fetchone()[0] == 1


def test_merge_and_rename_refuse_unsafe_requests(loaded):
    conn, data = loaded
    sheet_import.save(conn, data)
    for args in (("Fun", "Fun"), ("Uncategorised", "Fun"), ("Nope", "Fun"), ("Fun", "Nope")):
        with pytest.raises(ValueError):
            categorize.merge_category(conn, *args)
    with pytest.raises(ValueError):
        categorize.rename_category(conn, "Vituri", "Fun")            # already exists: merge instead
    with pytest.raises(ValueError):
        categorize.rename_category(conn, "Vituri", "  ")
    conn.execute("UPDATE batches SET status = 'committed'")
    with pytest.raises(PermissionError):
        categorize.merge_category(conn, "Vituri", "Medical")         # submitted numbers do not change silently
    conn.execute("UPDATE batches SET status = 'draft'")
    categorize.rename_category(conn, "Vituri", "Kids")
    assert _row(conn, "NEW CATEGORY")["cat"] == "Kids"


def test_the_setup_page_offers_category_tidying(home, loaded, tmp_path):
    conn, data = loaded
    sheet_import.save(conn, data); conn.commit()
    app = App(home, today="2026-10-05", app_dir=tmp_path)
    page = app.handle("GET", "/setup", H).body.decode()
    assert "Merge" in page and "Vituri" in page
    res = app.handle("POST", "/setup/category/merge", H, urlencode({"csrf": app.csrf, "src": "Vituri", "dst": "Medical"}).encode())
    from urllib.parse import unquote
    flash = unquote(next(v for k, v in res.headers if k == "Set-Cookie" and v.startswith("flash=")).split(";")[0][6:])
    assert "merged into" in flash and "1 lines moved" in flash


def test_vituri_is_separate_and_toggleable(tmp_path):
    from ftracker import summary
    conn = db.connect(tmp_path / "v.db")
    cat = conn.execute("SELECT id, reimbursed FROM categories WHERE name = 'Vituri'").fetchone()
    assert cat and cat["reimbursed"] == 1
    assert summary.include_reimbursed(conn) is False
    summary.set_include_reimbursed(conn, True)
    assert summary.include_reimbursed(conn) is True


def test_guess_from_similar_words_is_only_a_proposal(tmp_path):
    conn = db.connect(tmp_path / "g.db")
    cid = conn.execute("SELECT id FROM categories WHERE name = 'Medical'").fetchone()[0]
    conn.execute("INSERT INTO accounts (kind, issuer, label, currency, created_at) VALUES ('card','x','t','ILS','x')")
    a = conn.execute("SELECT id FROM accounts ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO batches (status, created_at) VALUES ('draft','x')")
    b = conn.execute("SELECT id FROM batches ORDER BY id DESC LIMIT 1").fetchone()[0]
    for i, d in enumerate(["מכבי דנט נס ציונה", "מכבי דנט רחובות", "מכבי דנט ראשון", "מכבי דנט חולון"]):
        conn.execute("""INSERT INTO transactions (batch_id, account_id, dedupe_key, txn_date, budget_month, description,
                        description_norm, amount, currency, category_id, category_status, kind, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (b, a, f"k{i}", "2025-01-01", "2025-01", d, d, -100.0, "ILS", cid, "approved", "purchase", "x"))
    got = categorize._guess(conn, "מכבידנט", -50.0)
    assert got and got[0] == cid
    assert categorize._guess(conn, "משהו אחר לגמרי", -50.0) is None


def test_duplicate_leumi_accounts_are_merged_with_their_balances(tmp_path):
    from ftracker import balances
    conn = db.connect(tmp_path / "m.db")
    a = accounts.add_account(conn, "bank", "manual", "לאומי")
    b = accounts.add_account(conn, "bank", "leumi", "Leumi 5939")
    balances.set_balance_for(conn, a, 100.0, "2026-01-01")
    balances.set_balance_for(conn, b, 250.0, "2026-02-01")
    assert firstrun.merge_duplicate_leumi(conn) is True
    rows = conn.execute("SELECT account_id, amount FROM balances ORDER BY as_of").fetchall()
    assert [(r["account_id"], r["amount"]) for r in rows] == [(b, 100.0), (b, 250.0)]
    assert conn.execute("SELECT active FROM accounts WHERE id = ?", (a,)).fetchone()[0] == 0
    assert firstrun.merge_duplicate_leumi(conn) is False
