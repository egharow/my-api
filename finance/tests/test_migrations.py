import sqlite3
from importlib import resources

from ftracker import seed
from ftracker.db import SCHEMA_VERSION, connect


def _make_v1(path):
    c = sqlite3.connect(path)
    c.executescript(resources.files("ftracker").joinpath("schema.sql").read_text(encoding="utf-8"))
    c.execute("PRAGMA user_version = 1")
    seed.seed_all(c)
    c.execute("INSERT INTO owners (name) VALUES ('Ely')")
    c.commit()
    c.close()


def test_a_first_release_database_is_upgraded_in_place_and_backed_up(tmp_path):
    db = tmp_path / "finance.db"
    _make_v1(db)
    conn = connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert conn.execute("SELECT name FROM owners").fetchone()[0] == "Ely"          # data kept
    conn.execute("INSERT INTO monthly_entries (month, section, label, source, created_at) VALUES ('2025-01','income','x','t','now')")
    conn.execute("INSERT INTO goals (name, target_amount, created_at) VALUES ('g', 1, 'now')")
    assert (tmp_path / "finance.before-upgrade-v1.db").exists()
    conn.close()


def test_a_new_database_is_not_backed_up_and_reopening_is_a_no_op(tmp_path):
    db = tmp_path / "finance.db"
    connect(db).close()
    assert not list(tmp_path.glob("*.before-upgrade*"))
    connect(db).close()
    assert not list(tmp_path.glob("*.before-upgrade*"))


def test_a_database_from_the_middle_commit_that_already_has_monthly_entries_still_upgrades(tmp_path):
    db = tmp_path / "finance.db"
    _make_v1(db)
    c = sqlite3.connect(db)
    c.executescript(resources.files("ftracker").joinpath("migrations", "002_monthly_entries.sql").read_text(encoding="utf-8"))
    c.close()                                                  # version still 1, table already there
    conn = connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_member_card_migration_fixes_an_existing_database(tmp_path):
    db = tmp_path / "finance.db"
    _make_v1(db)
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    for name in ("002_monthly_entries.sql", "003_goals.sql"):
        c.executescript(resources.files("ftracker").joinpath("migrations", name).read_text(encoding="utf-8"))
    c.execute("PRAGMA user_version = 3")
    # put it back the way the previous build left it: a card payment flagged as missing its statement
    card = c.execute("SELECT id FROM categories WHERE name = 'Card payment'").fetchone()[0]
    c.execute("UPDATE rules SET category_id = ?, set_kind = 'card_payment' WHERE pattern = 'מקס איט'", (card,))
    c.execute("INSERT INTO accounts (kind, issuer, label, last4, currency, created_at) VALUES ('bank','leumi','Leumi','1','ILS','n')")
    c.execute("INSERT INTO batches (created_at) VALUES ('n')")
    c.execute("""INSERT INTO transactions (batch_id, account_id, dedupe_key, txn_date, budget_month, description, description_norm,
                 amount, currency, kind, category_id, created_at) VALUES (1,1,'k','2026-09-10','2026-09','מקס איט פיננ-י','מקס איט פיננ-י',
                 -769,'ILS','card_payment',?,'n')""", (card,))
    c.execute("""INSERT INTO discrepancies (fingerprint, type, title, subject_type, subject_id, created_at)
                 VALUES ('missing_stmt:1','missing_statement','Bank paid 769 but no statement','transaction',1,'n')""")
    c.commit(); c.close()
    conn = connect(db)
    t = conn.execute("SELECT t.kind, c.name FROM transactions t JOIN categories c ON c.id = t.category_id").fetchone()
    assert tuple(t) == ("purchase", "Member card (בהצדעה)")
    assert conn.execute("SELECT status FROM discrepancies").fetchone()[0] == "resolved"
    assert conn.execute("SELECT set_kind FROM rules WHERE pattern = 'מקס איט'").fetchone()[0] is None
    conn.close()


def test_rates_guessed_from_card_charges_by_an_earlier_build_are_removed(tmp_path):
    db = tmp_path / "finance.db"
    _make_v1(db)
    c = sqlite3.connect(db)
    for name in ("002_monthly_entries.sql", "003_goals.sql", "004_member_card.sql"):
        c.executescript(resources.files("ftracker").joinpath("migrations", name).read_text(encoding="utf-8"))
    c.execute("PRAGMA user_version = 4")
    c.execute("INSERT INTO fx_rates VALUES ('2026-09-23','USD','ILS',3.0775,'card')")
    c.execute("INSERT INTO fx_rates VALUES ('2026-10-01','USD','ILS',3.6,'manual')")
    c.commit(); c.close()
    conn = connect(db)
    assert [tuple(r) for r in conn.execute("SELECT rate, source FROM fx_rates")] == [(3.6, "manual")]
    conn.close()
