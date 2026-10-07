-- Which account pays which card, by the card's last four digits (applied once the card exists, never over a choice made in Settings).
INSERT OR IGNORE INTO settings (key, value) VALUES ('card_payers', '{"6045": "One Zero", "6201": "external"}');
