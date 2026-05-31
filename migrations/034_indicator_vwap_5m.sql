-- Layer 4: intraday session-anchored VWAP on 5-minute bars.
-- Recomputed every minute during market hours by the live indicator scheduler.
-- Source is `raw_intraday_candles` (broker-backfilled 1-min, resampled to 5m)
-- plus today's live 1-min stream built from `raw_equity_quotes`.
--
-- Unlike the rolling RSI/MACD intraday indicators, VWAP is *session-anchored*:
-- the cumulative sums reset at each session's 09:15 open, so the value answers
-- "volume-weighted average price so far today", the standard intraday anchor.
--
-- `ts` is UTC epoch seconds at the 5-min bar start — matches the convention on
-- raw_intraday_candles. Dashboard adds IST_OFFSET for display. VWAP rides on
-- the price axis (an overlay), so it shares the close's scale.
--
-- Retention: rolling 30 calendar days, same as the other intraday indicator
-- tables. The nightly cleanup job DELETEs rows with ts < now() − 30d. See
-- src/nse_data/indicators/retention.py.

CREATE TABLE IF NOT EXISTS indicator_vwap_5m (
    symbol  TEXT    NOT NULL,
    ts      INTEGER NOT NULL,          -- UTC epoch seconds, 5-min bar start
    vwap    REAL,
    PRIMARY KEY (symbol, ts)
);

-- Per-symbol incremental reads: pull the latest N bars.
CREATE INDEX IF NOT EXISTS idx_indicator_vwap_5m_symbol_ts
    ON indicator_vwap_5m(symbol, ts DESC);

-- Retention sweep + cross-symbol time-range queries.
CREATE INDEX IF NOT EXISTS idx_indicator_vwap_5m_ts
    ON indicator_vwap_5m(ts DESC);
