-- Lines brought in by "Fetch cards" (straight from the card company's site) are marked, so the same purchase that later
-- arrives in a downloaded statement file, or the other way round, is recognised and not counted twice.
ALTER TABLE transactions ADD COLUMN source TEXT;
