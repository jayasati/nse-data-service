-- EMA 200 joins the server EMA pair on both cadences.
--
-- The dashboard had a client-side EMA200 overlay computed in the browser from
-- whatever bars the chart loaded — it drifted at the left edge and disagreed
-- with any server value. Persisting ema_200 next to ema_20/ema_50 lets the
-- server be the single source of truth for all three (and gives the bot the
-- classic 200-EMA regime anchor on both timeframes).

ALTER TABLE indicator_ema    ADD COLUMN ema_200 REAL;
ALTER TABLE indicator_ema_5m ADD COLUMN ema_200 REAL;
