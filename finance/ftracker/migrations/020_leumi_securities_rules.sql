-- Leumi account lines: buying securities and monthly deposits into the provident (Analyst) fund are savings, not spending.
INSERT INTO rules (source, pattern, direction, account_kind, category_id, set_kind, auto_approve, priority, created_at)
  SELECT 'builtin', 'קנית ני ע', 'out', 'bank', (SELECT id FROM categories WHERE name = 'Transfer to savings'), 'transfer', 1, 12, strftime('%Y-%m-%dT%H:%M:%S', 'now')
  WHERE EXISTS (SELECT 1 FROM categories WHERE name = 'Transfer to savings');
INSERT INTO rules (source, pattern, direction, account_kind, category_id, set_kind, auto_approve, priority, created_at)
  SELECT 'builtin', 'אנליסט קופ', 'out', 'bank', (SELECT id FROM categories WHERE name = 'Transfer to savings'), 'transfer', 1, 12, strftime('%Y-%m-%dT%H:%M:%S', 'now')
  WHERE EXISTS (SELECT 1 FROM categories WHERE name = 'Transfer to savings');
