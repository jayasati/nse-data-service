-- Intraday level set (single-session ATR fractions) alongside the swing targets.
ALTER TABLE conviction_daily ADD COLUMN intraday_stop REAL;
ALTER TABLE conviction_daily ADD COLUMN intraday_t1 REAL;
ALTER TABLE conviction_daily ADD COLUMN intraday_t2 REAL;
ALTER TABLE conviction_daily ADD COLUMN intraday_t3 REAL;
ALTER TABLE conviction_daily ADD COLUMN intraday_rr REAL;
