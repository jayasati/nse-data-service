-- Pre-print BFSI macro-risk flag on the earnings setup (Week 17.5, S7).
--
-- Stamped by events/pre_screen.py for bank/NBFC/insurer names whose result is
-- due, when the macro backdrop (raw_macro_rates) carries the risk DIRECTION that
-- predicted SBI's Q4: a recent repo cut (NIM compression) and/or rising 10Y
-- G-sec yields (treasury AFS mark-to-market loss). It cannot predict the number;
-- it says "be nervous about this print", applied across the whole BFSI universe.
-- NULL for non-BFSI names or a benign backdrop.

ALTER TABLE earnings_setups ADD COLUMN bfsi_macro_risk TEXT;  -- e.g. 'NIM_TREASURY_RISK'
