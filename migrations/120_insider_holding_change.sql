-- Calibration — store a robust holding_change_pct on raw_insider_trading. NSE's afterAcqSharesPer
-- is frequently a data-entry 0/blank (e.g. NTPC 70.96 -> "0" while share counts barely move), so
-- (aftPer - befPer) gives garbage (-71%). The COUNTS (befAcqSharesNo/afterAcqSharesNo) are reliable;
-- holding_change_pct = (aftNo - befNo)/befNo * befPer recovers the true % move. Promoter signals
-- read this column instead of differencing the noisy Per fields.
ALTER TABLE raw_insider_trading ADD COLUMN holding_change_pct REAL;
