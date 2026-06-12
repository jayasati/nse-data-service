-- Choppiness Index (14) on both cadences.
--
-- chop = 100 · log10( ΣTR(14) / (maxHigh(14) − minLow(14)) ) / log10(14)
-- Range 0–100: >61.8 = choppy/range-bound, <38.2 = trending. Pure rolling
-- window (no recursion), so no warm-up seed concerns beyond the 14 bars.
--
-- This replaces the dashboard's last client-computed study — the browser used
-- to compute CHOP from whatever bars the chart loaded, disagreeing with any
-- server value by construction. Same shapes as the other indicator tables.
--
-- Retention (5m): rolling 30 calendar days, registry-driven sweeper.
-- No secondary indexes: the (symbol, ts|date) PK covers per-symbol reads.

CREATE TABLE IF NOT EXISTS indicator_chop (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    chop_14 REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS indicator_chop_5m (
    symbol TEXT NOT NULL, ts INTEGER NOT NULL,
    chop_14 REAL,
    PRIMARY KEY (symbol, ts)
);
