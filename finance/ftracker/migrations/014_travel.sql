-- A Travel category, with rules for the obvious names (airlines, hotels, booking sites).
INSERT INTO categories (name, parent_id, kind, neutral)
  SELECT 'Travel', NULL, 'expense', 0 WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Travel');
-- Names that are clearly a trip: approved on their own.
INSERT INTO rules (source, pattern, category_id, auto_approve, priority, created_at)
  SELECT 'builtin', p, (SELECT id FROM categories WHERE name = 'Travel'), 1, length(p), strftime('%Y-%m-%dT%H:%M:%S', 'now')
  FROM (SELECT 'אל על' AS p UNION ALL SELECT 'ישראייר' UNION ALL SELECT 'ארקיע' UNION ALL SELECT 'BOOKING' UNION ALL SELECT 'AIRBNB'
        UNION ALL SELECT 'EXPEDIA' UNION ALL SELECT 'AGODA' UNION ALL SELECT 'RYANAIR' UNION ALL SELECT 'WIZZ' UNION ALL SELECT 'EASYJET'
        UNION ALL SELECT 'TRIPADVISOR' UNION ALL SELECT 'HOTELS COM' UNION ALL SELECT 'AIRWAY' UNION ALL SELECT 'AIRLINE'
        UNION ALL SELECT 'דיוטי פרי' UNION ALL SELECT 'DUTY FREE' UNION ALL SELECT 'מלון' UNION ALL SELECT 'טיסה' UNION ALL SELECT 'HOSTEL');
