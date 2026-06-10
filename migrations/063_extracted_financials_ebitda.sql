-- Week 18 (P3 follow-up): depreciation & finance-cost lines for non-bank EBITDA.
--
-- The non-bank operating line was core profit ex-other-income (PBT − other
-- income), derivable from already-stored fields. With depreciation and finance
-- costs we can compute true operating EBITDA
--   EBITDA = PBT + finance_cost + depreciation − other_income
-- the textbook operating line for energy / IT / FMCG / auto / metals. NULL for
-- BFSI (banks report interest expended, not these lines) and for any filing the
-- extractor doesn't read them from — the engine falls back to core-ex-OI.
ALTER TABLE extracted_financials ADD COLUMN depreciation_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN finance_cost_cr REAL;
