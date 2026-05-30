-- Layer 5: tag each backtest trade with its signal origin so the dashboard
-- can break down win-rate by signal type.
--
-- Used in v2 by strategies/macd_willr_daily/ to distinguish:
--   'basic'              — willr-hook + MACD-cross entry
--   'divergence'         — pivot-based bullish/bearish divergence
--   'basic+divergence'   — both fired the same bar
--
-- v1 (bb_ema9_30m) writes NULL — that strategy doesn't classify its signals.
-- Nullable, no index (3-4 distinct values; dashboard filters in JS).

ALTER TABLE backtest_trades ADD COLUMN signal_tags TEXT NULL;
