-- Balance-sheet base for the Quality/Valuation ratio factors (ROE, ROCE,
-- current-ratio, asset-turnover, P-B). Read from the result XBRL's period-end
-- INSTANT context (parsers/xbrl_financials.py). SEBI files the balance sheet
-- HALF-YEARLY, so these populate ~twice a year — enough for slow-moving ratios.
-- borrowings_cr / cash_cr are best-effort (many filers fold them into the
-- financial-asset/liability hierarchy without a summary Borrowings/Cash tag).
ALTER TABLE extracted_financials ADD COLUMN equity_cr REAL;            -- net worth (parent)
ALTER TABLE extracted_financials ADD COLUMN total_assets_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN current_assets_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN current_liabilities_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN total_liabilities_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN borrowings_cr REAL;       -- best-effort
ALTER TABLE extracted_financials ADD COLUMN cash_cr REAL;             -- best-effort
