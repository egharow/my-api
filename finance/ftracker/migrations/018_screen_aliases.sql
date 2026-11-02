-- What a line on a balance screenshot was matched to, so the next screenshot is matched by itself.
CREATE TABLE screen_aliases (
    section    TEXT NOT NULL DEFAULT '',
    label      TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    PRIMARY KEY (section, label)
);
