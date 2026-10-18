-- Online purchases are sorted by what was bought (clothes, home...), not by where. Remove the category an earlier build added.
UPDATE transactions SET category_id = (SELECT id FROM categories WHERE name = 'Uncategorised'), category_status = 'proposed'
  WHERE category_id = (SELECT id FROM categories WHERE name = 'Online shopping') AND category_status = 'proposed';
UPDATE transactions SET category_id = (SELECT id FROM categories WHERE name = 'Miscellaneous')
  WHERE category_id = (SELECT id FROM categories WHERE name = 'Online shopping');
DELETE FROM rules WHERE category_id = (SELECT id FROM categories WHERE name = 'Online shopping');
DELETE FROM categories WHERE name = 'Online shopping';
