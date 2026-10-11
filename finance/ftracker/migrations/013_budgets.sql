-- A monthly budget per spending category (the category as shown on Overview).
CREATE TABLE budgets (
    category TEXT PRIMARY KEY,
    monthly  REAL NOT NULL CHECK (monthly > 0)
);
