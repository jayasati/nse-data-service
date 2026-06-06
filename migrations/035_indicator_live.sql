-- Layer 4: live per-symbol indicator *snapshot*.
--
-- Unlike the time-series indicator tables (indicator_rsi_5m, indicator_vwap_5m,
-- ...), this holds exactly ONE row per symbol — the latest computed view used by
-- the signal engine. The live job rewrites the row every minute during market
-- hours (INSERT OR REPLACE on the symbol PK); the pre-market loader seeds it at
-- 08:45 with the previous session's final values so it is never empty at open.
--
-- It denormalises a handful of values that otherwise live in separate tables
-- (latest VWAP, latest RSI(5m)) plus three things computed only here: ATR(14)
-- on both daily and 5-min bars, the VWAP slope, and the trend/momentum tags.
-- Keeping them in one row makes the signal engine's "is this setup valid right
-- now?" check a single PK lookup instead of five joins.
--
-- `updated_at` is an ISO-8601 IST timestamp (when the row was last written).
-- See src/nse_data/indicators/live_snapshot.py for the writer and
-- src/nse_data/indicators/regime.py for the tag classifiers.

CREATE TABLE IF NOT EXISTS indicator_live (
    symbol          TEXT PRIMARY KEY,
    updated_at      TEXT NOT NULL,

    -- VWAP (session-anchored, from indicator_vwap_5m)
    vwap            REAL,
    vwap_slope      REAL,        -- (vwap_now - vwap_6_bars_ago) / 6  (30 min / 6 bars)
    price_vs_vwap   TEXT,        -- 'above' or 'below'

    -- ATR(14)
    atr_14_daily    REAL,        -- off the last ~15 daily bhavcopy candles
    atr_14_5m       REAL,        -- off the last ~15 five-minute bars

    -- RSI(5m) — copied from indicator_rsi_5m's latest bar
    rsi_5m          REAL,

    -- Regime tags
    trend_regime    TEXT,        -- strong_uptrend/uptrend/sideways/downtrend/strong_downtrend
    momentum_state  TEXT         -- overbought_extreme/overbought/bullish/neutral/bearish/oversold/oversold_extreme
);

-- The signal engine scans "all symbols currently in <regime>" — index the tag
-- so that filter doesn't full-scan the table every pass.
CREATE INDEX IF NOT EXISTS idx_indicator_live_trend_regime
    ON indicator_live(trend_regime);
