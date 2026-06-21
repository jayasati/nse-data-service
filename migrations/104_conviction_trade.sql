-- Stage-11 trade construction on the conviction snapshot (direction + levels for the UI).
ALTER TABLE conviction_daily ADD COLUMN direction TEXT;
ALTER TABLE conviction_daily ADD COLUMN entry REAL;
ALTER TABLE conviction_daily ADD COLUMN stop REAL;
ALTER TABLE conviction_daily ADD COLUMN t1 REAL;
ALTER TABLE conviction_daily ADD COLUMN t2 REAL;
ALTER TABLE conviction_daily ADD COLUMN t3 REAL;
ALTER TABLE conviction_daily ADD COLUMN rr REAL;
ALTER TABLE conviction_daily ADD COLUMN setup TEXT;
ALTER TABLE conviction_daily ADD COLUMN probability INTEGER;
