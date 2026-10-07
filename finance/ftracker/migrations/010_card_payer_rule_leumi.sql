-- Card 6201 is paid from Shir's Leumi account: use it once it has been imported, otherwise treat it as not imported.
UPDATE settings SET value = '{"6045": "One Zero", "6201": "leumi:Shir"}'
 WHERE key = 'card_payers' AND value = '{"6045": "One Zero", "6201": "external"}';
