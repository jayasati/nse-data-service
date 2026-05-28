-- Layer 4: intraday Relative Strength Index (14-period Wilder) on 5-minute bars.
-- Recomputed every minute during market hours by the live indicator scheduler.
-- Source is `raw_intraday_candles` (broker-backfilled 1-min, resampled to 5m)
-- plus today's live 1-min stream built from `raw_equity_quotes`.
--
-- `ts` is UTC epoch seconds at the 5-min bar start — matches the convention
-- on raw_intraday_candles. Dashboard adds IST_OFFSET for display.
--
-- Retention: rolling 30 calendar days. The nightly cleanup job DELETEs rows
-- with ts < now() − 30d. See src/nse_data/indicators/retention.py.

CREATE TABLE IF NOT EXISTS indicator_rsi_5m (
    symbol  TEXT    NOT NULL,
    ts      INTEGER NOT NULL,          -- UTC epoch seconds, 5-min bar start
    rsi_14  REAL,
    PRIMARY KEY (symbol, ts)
);

-- Per-symbol incremental reads: pull the latest N bars.
CREATE INDEX IF NOT EXISTS idx_indicator_rsi_5m_symbol_ts
    ON indicator_rsi_5m(symbol, ts DESC);

-- Retention sweep + cross-symbol time-range queries (e.g. signal engine
-- "all RSI dips in the last 15 minutes").
CREATE INDEX IF NOT EXISTS idx_indicator_rsi_5m_ts
    ON indicator_rsi_5m(ts DESC);
