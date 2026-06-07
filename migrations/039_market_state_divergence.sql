-- Intermarket divergence flags (FEATURE_CHECKLIST Phase 2, Week 9, task 9.6).
--
-- The regime job sets these when the tape is internally inconsistent — e.g.
-- Nifty rising while VIX rises with it (fragile rally), or banks falling while
-- the index holds flat (internal weakness). When either is set the confidence
-- scorer trims long confidence 10% session-wide, and alerts carry a ⚠ note.

ALTER TABLE market_state ADD COLUMN fragile_rally     INTEGER;   -- 0/1
ALTER TABLE market_state ADD COLUMN internal_weakness INTEGER;   -- 0/1
ALTER TABLE market_state ADD COLUMN regime_warnings   TEXT;      -- human-readable ⚠ note(s)
