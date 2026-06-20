-- Risk-based position sizing for the paper_book forward loop (PROFITABILITY_PLAN R4).
-- Each new position is sized to risk a fixed % of capital to an ATR-derived stop, so
-- the track record can be expressed in R-multiples (the proper expectancy unit) and the
-- exit P&L can use the real delivery cost model (R2) instead of a flat %.
-- All NULL on legacy rows opened before sizing existed; the engine degrades gracefully.
ALTER TABLE paper_book ADD COLUMN stop_px REAL;       -- ATR stop at entry (entry − k·ATR)
ALTER TABLE paper_book ADD COLUMN qty INTEGER;        -- shares (capital·risk% ÷ risk/share)
ALTER TABLE paper_book ADD COLUMN risk_rupees REAL;   -- 1R in ₹ = qty · (entry − stop)
ALTER TABLE paper_book ADD COLUMN net_pnl REAL;       -- realised ₹ net of the delivery cost model
ALTER TABLE paper_book ADD COLUMN r_multiple REAL;    -- net_pnl ÷ risk_rupees (P&L in R)
