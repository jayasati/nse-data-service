-- Financial-strength + value-trap screens for the pre-buy card (PROFITABILITY_PLAN R7+R8).
-- Nightly snapshot (fundamentals/strength_scores.py, 18:05 IST) from extracted_financials.
-- All NULL for symbols without extracted financials; the card degrades gracefully.
CREATE TABLE IF NOT EXISTS stock_strength (
    symbol             TEXT PRIMARY KEY,
    f_score            INTEGER,   -- Piotroski F (0-9) over computable signals (R8)
    f_signals          INTEGER,   -- how many of the 9 signals were computable
    interest_coverage  REAL,      -- (PBT + finance cost) / finance cost (R7)
    current_ratio      REAL,      -- current assets / current liabilities
    debt_equity        REAL,      -- borrowings / equity
    bs_score           REAL,      -- 0-100 balance-sheet strength (R7)
    distress           TEXT,      -- csv of red flags (negative_net_worth, ...); NULL if none
    updated_date       TEXT
);
