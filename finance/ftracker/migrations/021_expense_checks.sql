-- "Did this really happen?" for each payment, separate from whether its category was approved.
ALTER TABLE transactions ADD COLUMN check_state TEXT CHECK (check_state IN ('ok', 'question'));
ALTER TABLE transactions ADD COLUMN check_by TEXT;
ALTER TABLE transactions ADD COLUMN check_at TEXT;
ALTER TABLE transactions ADD COLUMN check_reason TEXT;
ALTER TABLE transactions ADD COLUMN check_note TEXT;
CREATE INDEX idx_transactions_check ON transactions (budget_month, check_state);
