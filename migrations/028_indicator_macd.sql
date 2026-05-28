-- Layer 4: daily MACD (12/26/9, standard).
-- One row per (symbol, date). All three series in one row because they share
-- input + warm-up and are always read together (line/signal cross + histogram
-- divergence are the canonical signals).
--
--   macd        = EMA(close, 12) − EMA(close, 26)
--   macd_signal = EMA(macd, 9)
--   macd_hist   = macd − macd_signal
--
-- NULL during warm-up. Computed incrementally — see
-- src/nse_data/indicators/momentum/macd.py.

CREATE TABLE IF NOT EXISTS indicator_macd (
    symbol       TEXT NOT NULL,
    date         TEXT NOT NULL,
    macd         REAL,
    macd_signal  REAL,
    macd_hist    REAL,
    PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_indicator_macd_date
    ON indicator_macd(date DESC);
