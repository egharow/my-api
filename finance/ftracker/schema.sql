-- Amounts are signed cash flow from the account holder's view:
-- money out is negative, money in is positive. Statement totals are positive.

CREATE TABLE owners (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE
);

-- Text found on a statement that identifies an owner. Matched, then discarded:
-- holder names are never stored on transactions or statements.
CREATE TABLE owner_aliases (
    id        INTEGER PRIMARY KEY,
    owner_id  INTEGER NOT NULL REFERENCES owners(id),
    alias     TEXT NOT NULL UNIQUE
);

CREATE TABLE accounts (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN
                  ('card','bank','investment','pension','savings','real_estate','loan','other')),
    issuer      TEXT NOT NULL,
    label       TEXT NOT NULL,
    last4       TEXT,
    owner_id    INTEGER REFERENCES owners(id),
    currency    TEXT NOT NULL DEFAULT 'ILS',
    pays_from_account_id INTEGER REFERENCES accounts(id),
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    UNIQUE (issuer, last4)
);

CREATE TABLE batches (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','committed','reopened')),
    committed_at  TEXT,
    note          TEXT
);

CREATE TABLE commits (
    id            INTEGER PRIMARY KEY,
    batch_id      INTEGER NOT NULL REFERENCES batches(id),
    version       INTEGER NOT NULL,
    committed_at  TEXT NOT NULL,
    reason        TEXT,
    summary_json  TEXT NOT NULL,
    backup_path   TEXT,
    UNIQUE (batch_id, version)
);

CREATE TABLE source_files (
    id             INTEGER PRIMARY KEY,
    sha256         TEXT NOT NULL UNIQUE,
    original_name  TEXT NOT NULL,
    issuer         TEXT NOT NULL,
    file_kind      TEXT NOT NULL,
    archived_path  TEXT,
    period_start   TEXT,
    period_end     TEXT,
    batch_id       INTEGER NOT NULL REFERENCES batches(id),
    imported_at    TEXT NOT NULL
);

CREATE TABLE statements (
    id              INTEGER PRIMARY KEY,
    source_file_id  INTEGER NOT NULL REFERENCES source_files(id),
    account_id      INTEGER NOT NULL REFERENCES accounts(id),
    billing_date    TEXT,
    period_start    TEXT,
    period_end      TEXT,
    stated_total    REAL,
    computed_total  REAL,
    currency        TEXT NOT NULL DEFAULT 'ILS',
    matched_bank_txn_id INTEGER,
    UNIQUE (account_id, billing_date)
);

CREATE TABLE categories (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    parent_id  INTEGER REFERENCES categories(id),
    kind       TEXT NOT NULL CHECK (kind IN ('expense','income','transfer','debt','saving','fee')),
    neutral    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE transactions (
    id               INTEGER PRIMARY KEY,
    batch_id         INTEGER NOT NULL REFERENCES batches(id),
    account_id       INTEGER NOT NULL REFERENCES accounts(id),
    statement_id     INTEGER REFERENCES statements(id),
    dedupe_key       TEXT NOT NULL UNIQUE,
    txn_date         TEXT NOT NULL,
    billing_date     TEXT,
    budget_month     TEXT NOT NULL,
    description      TEXT NOT NULL,
    description_norm TEXT NOT NULL,
    detail           TEXT,
    amount           REAL NOT NULL,
    currency         TEXT NOT NULL,
    orig_amount      REAL,
    orig_currency    TEXT,
    fx_rate          REAL,
    voucher          TEXT,
    balance_after    REAL,
    kind             TEXT NOT NULL DEFAULT 'purchase' CHECK (kind IN
                       ('purchase','refund','fee','income','transfer','card_payment',
                        'loan','mortgage','interest','other')),
    is_recurring     INTEGER NOT NULL DEFAULT 0,
    instalment_no    INTEGER,
    instalment_total INTEGER,
    instalment_full  REAL,
    category_id      INTEGER REFERENCES categories(id),
    category_status  TEXT NOT NULL DEFAULT 'proposed' CHECK (category_status IN ('proposed','approved')),
    confidence       REAL,
    proposal_basis   TEXT,
    counterparty_account_id INTEGER REFERENCES accounts(id),
    expects_refund_by TEXT,
    refund_of_txn_id INTEGER REFERENCES transactions(id),
    created_at       TEXT NOT NULL
);
CREATE INDEX idx_txn_month ON transactions(budget_month);
CREATE INDEX idx_txn_account ON transactions(account_id, txn_date);
CREATE INDEX idx_txn_norm ON transactions(description_norm);

-- Learned and built-in categorisation rules. Highest priority wins; user rules
-- outrank learned rules outrank built-in ones.
CREATE TABLE rules (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL CHECK (source IN ('builtin','learned','user')),
    pattern       TEXT NOT NULL,
    is_regex      INTEGER NOT NULL DEFAULT 0,
    direction     TEXT CHECK (direction IN ('in','out')),
    min_amount    REAL,
    max_amount    REAL,
    account_kind  TEXT,
    category_id   INTEGER REFERENCES categories(id),
    set_kind      TEXT,
    counterparty_account_id INTEGER REFERENCES accounts(id),
    auto_approve  INTEGER NOT NULL DEFAULT 0,
    expects_refund_days INTEGER,
    priority      INTEGER NOT NULL DEFAULT 0,
    enabled       INTEGER NOT NULL DEFAULT 1,
    note          TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE discrepancies (
    id             INTEGER PRIMARY KEY,
    fingerprint    TEXT NOT NULL UNIQUE,
    type           TEXT NOT NULL,
    severity       TEXT NOT NULL DEFAULT 'warning' CHECK (severity IN ('info','warning','error')),
    title          TEXT NOT NULL,
    detail         TEXT,
    subject_type   TEXT,
    subject_id     INTEGER,
    batch_id       INTEGER REFERENCES batches(id),
    status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','explained','resolved','acknowledged')),
    auto_resolved  INTEGER NOT NULL DEFAULT 0,
    follow_up_date TEXT,
    created_at     TEXT NOT NULL,
    resolved_at    TEXT
);

CREATE TABLE comments (
    id             INTEGER PRIMARY KEY,
    discrepancy_id INTEGER NOT NULL REFERENCES discrepancies(id),
    author         TEXT NOT NULL,
    body           TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    edited_at      TEXT,
    deleted        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE fx_rates (
    rate_date TEXT NOT NULL,
    base      TEXT NOT NULL,
    quote     TEXT NOT NULL,
    rate      REAL NOT NULL,
    source    TEXT NOT NULL,
    PRIMARY KEY (rate_date, base, quote)
);

CREATE TABLE balances (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    as_of       TEXT NOT NULL,
    amount      REAL NOT NULL,
    currency    TEXT NOT NULL,
    batch_id    INTEGER REFERENCES batches(id),
    note        TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (account_id, as_of)
);

-- Months that earlier data already covers (for example the imported Google Sheet),
-- so the expected-files check does not ask for statements you no longer need.
CREATE TABLE coverage (
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    month      TEXT NOT NULL,
    source     TEXT NOT NULL,
    PRIMARY KEY (account_id, month)
);

CREATE TABLE audit_log (
    id       INTEGER PRIMARY KEY,
    ts       TEXT NOT NULL,
    actor    TEXT NOT NULL,
    action   TEXT NOT NULL,
    entity   TEXT,
    entity_id INTEGER,
    detail   TEXT
);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Monthly figures that are not line items in any statement: income, savings, debt payments
-- and budget targets, imported from the old budget sheet.
CREATE TABLE monthly_entries (
    id        INTEGER PRIMARY KEY,
    month     TEXT NOT NULL,
    section   TEXT NOT NULL CHECK (section IN ('income','saving','debt','budget')),
    label     TEXT NOT NULL,
    owner_id  INTEGER REFERENCES owners(id),
    expected  REAL,
    actual    REAL,
    source    TEXT NOT NULL,
    batch_id  INTEGER REFERENCES batches(id),
    created_at TEXT NOT NULL,
    UNIQUE (month, section, label, source)
);
