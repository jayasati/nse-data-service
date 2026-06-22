-- Replace the falsely-precise probability % with an honest RELATIVE conviction band
-- (High / Medium / Low), gated by confluence. NOT a calibrated win-rate — the engine has no forward
-- track record yet. The legacy `probability` column is left in place but no longer populated; once
-- conviction_factor_log + the paper-trade accumulate closed outcomes we attach the realized hit-rate
-- per band (then, and only then, a number is justified).
ALTER TABLE conviction_daily ADD COLUMN conviction_band TEXT;
