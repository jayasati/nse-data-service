-- Layer 4: per-stock daily Simple Moving Averages.
-- One row per (symbol, date); columns are the standard SMA windows used
-- across the trend stack (20, 50, 200). NULL until enough history exists.
--
-- Computed incrementally by the indicators nightly job — see
-- src/nse_data/indicators/trend/sma.py.

CREATE TABLE IF NOT EXISTS indicator_sma (
    symbol  TEXT NOT NULL,
    date    TEXT NOT NULL,
    sma_20  REAL,
    sma_50  REAL,
    sma_200 REAL,
    PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_indicator_sma_date
    ON indicator_sma(date DESC);
