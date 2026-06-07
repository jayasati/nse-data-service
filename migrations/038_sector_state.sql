-- Sector radar (FEATURE_CHECKLIST Phase 2, Week 8, task 8.1).
--
-- One row per (sector, 5-minute snapshot): relative strength of each NSE
-- sectoral index vs NIFTY 50, its rank among peers, and whether that strength
-- is improving or fading. `market/sector_radar_job.py` upserts this every 5
-- minutes during market hours; the confidence scorer (task 8.4) reads the
-- latest rank/trend for a signal's sector.
--
-- `as_of` is an ISO-8601 IST string (same convention as market_state /
-- indicator_live). `rs_ratio` is kept for display; ranking is done on excess
-- return (sector_return - nifty_return), which is stable when Nifty is flat.

CREATE TABLE IF NOT EXISTS sector_state (
    sector_name        TEXT NOT NULL,      -- e.g. 'NIFTY BANK'
    as_of              TEXT NOT NULL,      -- ISO-8601 IST snapshot time
    rs_ratio           REAL,               -- sector_return / nifty_return (guarded; null near flat)
    rs_rank            INTEGER,            -- 1 (best) .. 11 (worst), by excess return
    rs_trend           TEXT,               -- 'improving' / 'flat' / 'deteriorating' (vs ~30m ago)
    volume_state       TEXT,               -- 'above_avg'/'normal'/'below_avg' (null: not yet derived)
    sector_return_pct  REAL,               -- the sector index's session pct_change
    PRIMARY KEY (sector_name, as_of)
);

CREATE INDEX IF NOT EXISTS idx_sector_state_asof ON sector_state(as_of DESC);
