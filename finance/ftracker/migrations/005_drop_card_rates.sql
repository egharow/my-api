-- Earlier builds guessed a dollar rate from card charges. That is a stale, margin-laden number and
-- expenses never need a rate, so remove them. Dollar assets use the current rate (fetched or typed).
DELETE FROM fx_rates WHERE source = 'card';
