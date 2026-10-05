-- Monthly figures that are not line items in any statement: income, savings, debt payments
-- and budget targets, imported from the old budget sheet.
CREATE TABLE IF NOT EXISTS monthly_entries (
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
