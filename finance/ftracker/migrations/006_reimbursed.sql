-- Expenses you are paid back for in cash (Vituri). They stay a category of their own, and a switch
-- on the dashboard decides whether they count in spending totals.
ALTER TABLE categories ADD COLUMN reimbursed INTEGER NOT NULL DEFAULT 0;
INSERT INTO categories (name, parent_id, kind, neutral)
  SELECT 'Vituri', NULL, 'expense', 0 WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Vituri');
UPDATE categories SET reimbursed = 1 WHERE name = 'Vituri';
INSERT OR IGNORE INTO settings (key, value) VALUES ('include_reimbursed', '0');
