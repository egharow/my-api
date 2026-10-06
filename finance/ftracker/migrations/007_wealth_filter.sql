-- Choose which accounts count in net worth (for example leave out a pension fund or real estate).
ALTER TABLE accounts ADD COLUMN in_wealth INTEGER NOT NULL DEFAULT 1;
