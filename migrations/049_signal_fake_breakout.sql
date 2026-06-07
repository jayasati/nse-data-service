-- Fake-breakout risk flag on signals (FEATURE_CHECKLIST Phase 4, Week 15, 15.3).
--
-- Set on a breakout_52wh signal when the breakout bar shows wick rejection
-- (close in the lower half of the bar's range) on unconvincing volume. The
-- dispatcher trims confidence by 0.10 when this is set.

ALTER TABLE signals ADD COLUMN fake_breakout_risk INTEGER DEFAULT 0;
