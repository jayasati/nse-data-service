-- Analysis-important P&L/cost/quality fields NSE publishes in the result XBRL
-- but we were dropping (only ~14 of ~45 facts were captured).
--
-- These unlock proper sector analysis: the cost structure (materials/employee/
-- other-expenses) gives gross margin & bottom-up EBITDA; the earnings-quality
-- lines (exceptional items, current vs deferred tax, PBT-before-exceptional)
-- power the prop guards directly instead of inferring them from growth; and the
-- bank block adds opex, NPA amounts, CET1 capital adequacy and ROA — headline
-- bank health metrics. All crore except cet1_ratio / return_on_assets (percent).

-- cost structure (non-bank) — for gross margin & bottom-up EBITDA
ALTER TABLE extracted_financials ADD COLUMN cost_of_materials_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN purchases_of_stock_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN change_in_inventory_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN employee_cost_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN other_expenses_cr REAL;

-- earnings quality — the prop/one-off detectors
ALTER TABLE extracted_financials ADD COLUMN exceptional_items_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN pbt_before_exceptional_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN current_tax_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN deferred_tax_cr REAL;

-- below-the-line / comprehensive
ALTER TABLE extracted_financials ADD COLUMN share_of_associates_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN other_comprehensive_income_cr REAL;

-- bank block
ALTER TABLE extracted_financials ADD COLUMN operating_expenses_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN gross_npa_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN net_npa_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN cet1_ratio REAL;       -- percent
ALTER TABLE extracted_financials ADD COLUMN return_on_assets REAL; -- percent
