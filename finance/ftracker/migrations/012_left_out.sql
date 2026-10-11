-- A payment you chose to leave out of the spending numbers (a one-off like a car), kept in the records.
ALTER TABLE transactions ADD COLUMN left_out INTEGER NOT NULL DEFAULT 0;
