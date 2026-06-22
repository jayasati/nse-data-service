-- Surface ORB + live price + rvol into the per-symbol snapshot for the /intraday board.
ALTER TABLE indicator_live ADD COLUMN ltp REAL;
ALTER TABLE indicator_live ADD COLUMN orb_high REAL;
ALTER TABLE indicator_live ADD COLUMN orb_low REAL;
ALTER TABLE indicator_live ADD COLUMN orb_break REAL;
ALTER TABLE indicator_live ADD COLUMN rvol_5m REAL;
ALTER TABLE indicator_live ADD COLUMN structure_5m TEXT;
