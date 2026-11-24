-- Card charges the site shows as not final yet ("בקליטה"). Kept apart from transactions so they never count in any total.
-- Each fetch replaces a login's list with what the site shows then; a charge that became final arrives as a normal line.
CREATE TABLE pending_charges (
    id            INTEGER PRIMARY KEY,
    login_id      TEXT NOT NULL,
    account_id    INTEGER REFERENCES accounts(id),
    txn_date      TEXT NOT NULL,
    description   TEXT NOT NULL,
    amount        REAL NOT NULL,
    currency      TEXT NOT NULL DEFAULT 'ILS',
    orig_amount   REAL,
    orig_currency TEXT,
    fetched_at    TEXT NOT NULL
);
CREATE INDEX idx_pending_login ON pending_charges (login_id);
