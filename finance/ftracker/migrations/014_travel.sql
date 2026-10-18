-- Travel and Online shopping categories, with rules for the obvious names (airlines, hotels, booking sites, big marketplaces).
INSERT INTO categories (name, parent_id, kind, neutral)
  SELECT 'Travel', NULL, 'expense', 0 WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Travel');
INSERT INTO categories (name, parent_id, kind, neutral)
  SELECT 'Online shopping', NULL, 'expense', 0 WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Online shopping');
-- Names that are clearly a trip: approved on their own.
INSERT INTO rules (source, pattern, category_id, auto_approve, priority, created_at)
  SELECT 'builtin', p, (SELECT id FROM categories WHERE name = 'Travel'), 1, length(p), strftime('%Y-%m-%dT%H:%M:%S', 'now')
  FROM (SELECT 'אל על' AS p UNION ALL SELECT 'ישראייר' UNION ALL SELECT 'ארקיע' UNION ALL SELECT 'BOOKING' UNION ALL SELECT 'AIRBNB'
        UNION ALL SELECT 'EXPEDIA' UNION ALL SELECT 'AGODA' UNION ALL SELECT 'RYANAIR' UNION ALL SELECT 'WIZZ' UNION ALL SELECT 'EASYJET'
        UNION ALL SELECT 'TRIPADVISOR' UNION ALL SELECT 'HOTELS COM' UNION ALL SELECT 'AIRWAY' UNION ALL SELECT 'AIRLINE'
        UNION ALL SELECT 'דיוטי פרי' UNION ALL SELECT 'DUTY FREE' UNION ALL SELECT 'מלון' UNION ALL SELECT 'טיסה' UNION ALL SELECT 'HOSTEL');
-- Marketplaces: only suggested, you confirm.
INSERT INTO rules (source, pattern, category_id, auto_approve, priority, created_at)
  SELECT 'builtin', p, (SELECT id FROM categories WHERE name = 'Online shopping'), 0, length(p), strftime('%Y-%m-%dT%H:%M:%S', 'now')
  FROM (SELECT 'AMAZON' AS p UNION ALL SELECT 'MAZON' UNION ALL SELECT 'MKTPL' UNION ALL SELECT 'ALIEXPRESS' UNION ALL SELECT 'LIEXPRESS' UNION ALL SELECT 'TEMU'
        UNION ALL SELECT 'SHEIN' UNION ALL SELECT 'EBAY' UNION ALL SELECT 'ETSY');
