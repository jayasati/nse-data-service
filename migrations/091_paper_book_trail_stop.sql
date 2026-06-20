-- Chandelier trailing stop for the paper_book loop (PROFITABILITY_PLAN R10).
-- Ratchets up to HH(window) − k·ATR as a position gains, locking it in (never down),
-- sitting above the initial ATR stop (the R floor). NULL until the position trails.
ALTER TABLE paper_book ADD COLUMN trail_stop REAL;
