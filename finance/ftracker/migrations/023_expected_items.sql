-- Things that should arrive (or be paid) every month, like child allowance or rent. A missing one, or one that comes out
-- different from usual, is flagged.
CREATE TABLE expected_items (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    pattern    TEXT NOT NULL,
    direction  TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    amount     REAL,
    tolerance  REAL NOT NULL DEFAULT 0.08,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
