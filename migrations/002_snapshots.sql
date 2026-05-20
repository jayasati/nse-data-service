-- Layer 2 / Phase 3: raw_oi_spurts
-- Snapshot of OI activity for F&O underlyings. Re-polled every 5 min during
-- market hours; each poll produces a fresh row keyed by (symbol, as_of).
--
-- NSE's /api/live-analysis-oi-spurts-underlyings returns OI levels and the
-- underlying spot value. Price-change derivation (needed for the buildup
-- pattern classification in §8.1) happens in Layer 4 by joining against
-- raw_equity_quotes — not here. Layer 2 stores facts; Layer 4 computes.

CREATE TABLE IF NOT EXISTS raw_oi_spurts (
    symbol            TEXT NOT NULL,
    as_of             INTEGER NOT NULL,
    latest_oi         INTEGER,
    prev_oi           INTEGER,
    change_in_oi      INTEGER,
    avg_oi_pct        REAL,
    volume            INTEGER,
    underlying_value  REAL,
    PRIMARY KEY (symbol, as_of)
);

CREATE INDEX IF NOT EXISTS idx_oi_spurts_asof
    ON raw_oi_spurts(as_of DESC);

CREATE INDEX IF NOT EXISTS idx_oi_spurts_symbol_asof
    ON raw_oi_spurts(symbol, as_of DESC);