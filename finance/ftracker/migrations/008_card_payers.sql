-- A card can be paid from an account that is not imported (for example a partner's bank account).
ALTER TABLE accounts ADD COLUMN pays_externally INTEGER NOT NULL DEFAULT 0;
