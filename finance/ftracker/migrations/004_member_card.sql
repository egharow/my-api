-- The member card (בהצדעה) statement is optional, so its payments count as spending
-- instead of being flagged as a card payment with a missing statement.
INSERT OR IGNORE INTO categories (name, parent_id, kind, neutral) VALUES ('Member card (בהצדעה)', NULL, 'expense', 0);

UPDATE rules
   SET category_id = (SELECT id FROM categories WHERE name = 'Member card (בהצדעה)'),
       set_kind = NULL, note = 'member card; statement optional'
 WHERE source = 'builtin' AND pattern = 'מקס איט';

UPDATE transactions
   SET kind = 'purchase',
       category_id = (SELECT id FROM categories WHERE name = 'Member card (בהצדעה)'),
       category_status = 'approved', proposal_basis = 'member card payment'
 WHERE description_norm LIKE '%מקס איט%' AND kind = 'card_payment'
   AND batch_id IN (SELECT id FROM batches WHERE status != 'committed');

UPDATE discrepancies
   SET status = 'resolved', auto_resolved = 1, resolved_at = datetime('now')
 WHERE type IN ('missing_statement', 'payment_mismatch') AND status = 'open' AND subject_type = 'transaction'
   AND subject_id IN (SELECT id FROM transactions WHERE kind != 'card_payment');
