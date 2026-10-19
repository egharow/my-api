-- Trips and tags: a named group of payments. A trip also has dates, and everything spent in those dates belongs to it.
CREATE TABLE tags (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL CHECK (kind IN ('trip', 'tag')),
    start_date TEXT,
    end_date   TEXT,
    note       TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE transaction_tags (
    tag_id         INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    included       INTEGER NOT NULL DEFAULT 1,      -- 1: added by you, 0: taken out of the trip's dates
    PRIMARY KEY (tag_id, transaction_id)
);
