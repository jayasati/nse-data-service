-- BFSI operating lines + PDF-derived growth (Phase 5 — Week 17.5, S2).
--
-- For a bank/NBFC, pat_cr hides the operating story the market actually prices.
-- These columns persist the BFSI lines the sector-aware extractor (S1) reads:
-- net interest income, pre-provision operating profit (PPOP), provisions, the
-- treasury (profit-on-sale-of-investments) line, asset quality (GNPA/NNPA %),
-- and slippages. Populated only for BFSI symbols; NULL for everyone else.
--
-- growth_json stores the YoY/QoQ percentages the extractor computes from the
-- PDF's OWN comparative columns (no stored history needed) — the divergence
-- inputs the result_quality signal (S3) reads at detection time, so it works on
-- a symbol's first stored result (e.g. SBI's Q4 print) with no prior quarter.

ALTER TABLE extracted_financials ADD COLUMN interest_earned_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN interest_expended_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN net_interest_income_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN operating_profit_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN provisions_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN profit_on_sale_of_investments_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN gross_npa_pct REAL;
ALTER TABLE extracted_financials ADD COLUMN net_npa_pct REAL;
ALTER TABLE extracted_financials ADD COLUMN slippages_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN growth_json TEXT;
