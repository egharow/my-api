-- Order accounts the way you moved them, and let an account be closed (kept in the past, left out from the closing date on).
ALTER TABLE accounts ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE accounts ADD COLUMN closed_on TEXT;
-- The card payer rule follows the account name you use now.
UPDATE settings SET value = REPLACE(value, 'One Zero', 'וואן זירו') WHERE key = 'card_payers';
