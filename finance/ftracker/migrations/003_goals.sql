ALTER TABLE owners ADD COLUMN birth_date TEXT;

CREATE TABLE goals (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    target_amount REAL NOT NULL,
    currency      TEXT NOT NULL DEFAULT 'ILS',
    target_date   TEXT,
    target_age    INTEGER,
    owner_id      INTEGER REFERENCES owners(id),
    annual_return REAL NOT NULL DEFAULT 0,
    note          TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);

-- Which accounts count toward a goal. With none linked, the goal tracks total net worth.
CREATE TABLE goal_accounts (
    goal_id    INTEGER NOT NULL REFERENCES goals(id),
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    share      REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (goal_id, account_id)
);
