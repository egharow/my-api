-- One payment that is really two things (for example a municipal bill: property tax plus water).
ALTER TABLE transactions ADD COLUMN split_of INTEGER REFERENCES transactions(id);
ALTER TABLE transactions ADD COLUMN split_total REAL;
CREATE INDEX idx_transactions_split ON transactions (split_of);
CREATE TABLE split_rules (
    id                INTEGER PRIMARY KEY,
    pattern           TEXT NOT NULL,
    fixed_amount      REAL NOT NULL CHECK (fixed_amount > 0),
    fixed_category_id INTEGER NOT NULL REFERENCES categories(id),
    rest_category_id  INTEGER NOT NULL REFERENCES categories(id),
    enabled           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL
);
