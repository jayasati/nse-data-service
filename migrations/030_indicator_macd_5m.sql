-- Layer 4: intraday MACD (12/26/9) on 5-minute bars.
-- Recomputed every minute during market hours by the live indicator scheduler.
-- Source: raw_intraday_candles (broker-backfilled 1-min) merged with today's
-- live 1-min stream built from raw_equity_quotes, then resampled to 5m.
--
-- `ts` is UTC epoch seconds at the 5-min bar start (matches raw_intraday_candles).
-- Three series stored in one row because they share input + warm-up and the
-- canonical signals (line/signal cross, hist sign-flip) need them together.
--
-- Retention: rolling 30 calendar days. See src/nse_data/indicators/retention.py.

CREATE TABLE IF NOT EXISTS indicator_macd_5m (
    symbol       TEXT    NOT NULL,
    ts           INTEGER NOT NULL,
    macd         REAL,
    macd_signal  REAL,
    macd_hist    REAL,
    PRIMARY KEY (symbol, ts)
);

CREATE INDEX IF NOT EXISTS idx_indicator_macd_5m_symbol_ts
    ON indicator_macd_5m(symbol, ts DESC);

CREATE INDEX IF NOT EXISTS idx_indicator_macd_5m_ts
    ON indicator_macd_5m(ts DESC);
