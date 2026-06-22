-- Per-stock NSE pre-open indicative open (IEP) + real gap, wired into the conviction snapshot.
ALTER TABLE conviction_daily ADD COLUMN open_iep REAL;
ALTER TABLE conviction_daily ADD COLUMN gap_pct REAL;
