-- Layer 4: daily Relative Strength Index (Wilder smoothing).
-- One row per (symbol, date). Value is bounded 0–100; NULL during the 14-bar
-- warm-up window.
--
-- Computed incrementally by the indicators nightly job — see
-- src/nse_data/indicators/momentum/rsi.py.

CREATE TABLE IF NOT EXISTS indicator_rsi (
    symbol  TEXT NOT NULL,
    date    TEXT NOT NULL,
    rsi_14  REAL,
    PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_indicator_rsi_date
    ON indicator_rsi(date DESC);
