-- Balance-sheet working-capital inputs for the capgoods working_capital_balloon
-- guard (and useful for every non-financial). The quarterly result XBRL carries
-- no cash-flow statement, but it DOES carry the period-end balance sheet — so we
-- read trade receivables, inventories and trade payables (each summing current +
-- non-current) from the instant context. Working capital = receivables +
-- inventories − payables; a ballooning WC-to-revenue ratio is the capgoods tell
-- (execution funded by stuck cash, not converted to profit). All crore.

ALTER TABLE extracted_financials ADD COLUMN trade_receivables_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN inventories_cr REAL;
ALTER TABLE extracted_financials ADD COLUMN trade_payables_cr REAL;
