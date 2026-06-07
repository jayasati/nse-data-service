-- Intraday Supertrend + Volume Delta (FEATURE_CHECKLIST Phase 4, Week 12, 12.4).
--
-- 5-minute series recomputed every minute during the session (cadence=intraday),
-- alongside the existing rsi_5m / macd_5m / vwap_5m. The latest value of each is
-- surfaced into indicator_live for the live decision path.

CREATE TABLE IF NOT EXISTS indicator_supertrend_5m (
    symbol         TEXT NOT NULL,
    ts             INTEGER NOT NULL,   -- 5-min bar start, UTC epoch
    supertrend     REAL,
    supertrend_dir INTEGER,            -- 1 = up/long, -1 = down/short
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS indicator_volume_delta_5m (
    symbol        TEXT NOT NULL,
    ts            INTEGER NOT NULL,
    vol_delta     REAL,                -- signed volume this bar (buy − sell proxy)
    cum_vol_delta REAL,                -- running delta over the read window
    PRIMARY KEY (symbol, ts)
);

ALTER TABLE indicator_live ADD COLUMN supertrend_5m_dir INTEGER;
ALTER TABLE indicator_live ADD COLUMN vol_delta REAL;
