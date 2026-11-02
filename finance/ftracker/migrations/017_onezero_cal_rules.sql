-- Rules for One Zero account lines (trades in securities, subscription fee, card payments to Isracard Direct).
INSERT INTO rules (source, pattern, account_kind, category_id, set_kind, auto_approve, priority, created_at)
  SELECT 'builtin', 'ניירות ערך', 'bank', (SELECT id FROM categories WHERE name = 'Transfer to savings'), 'transfer', 1, 10, strftime('%Y-%m-%dT%H:%M:%S', 'now')
  WHERE EXISTS (SELECT 1 FROM categories WHERE name = 'Transfer to savings');
INSERT INTO rules (source, pattern, account_kind, category_id, set_kind, auto_approve, priority, created_at)
  SELECT 'builtin', 'דמי מנוי', 'bank', (SELECT id FROM categories WHERE name = 'Bank fees'), 'fee', 1, 8, strftime('%Y-%m-%dT%H:%M:%S', 'now')
  WHERE EXISTS (SELECT 1 FROM categories WHERE name = 'Bank fees');
INSERT INTO rules (source, pattern, direction, account_kind, category_id, set_kind, auto_approve, priority, created_at)
  SELECT 'builtin', 'ישראכרט-דיירקט', 'out', 'bank', (SELECT id FROM categories WHERE name = 'Card payment'), 'card_payment', 1, 14, strftime('%Y-%m-%dT%H:%M:%S', 'now')
  WHERE EXISTS (SELECT 1 FROM categories WHERE name = 'Card payment');
INSERT INTO rules (source, pattern, direction, account_kind, category_id, auto_approve, priority, created_at)
  SELECT 'builtin', 'ישראכרט-דיירקט', 'in', 'bank', (SELECT id FROM categories WHERE name = 'Other income'), 0, 14, strftime('%Y-%m-%dT%H:%M:%S', 'now')
  WHERE EXISTS (SELECT 1 FROM categories WHERE name = 'Other income');
