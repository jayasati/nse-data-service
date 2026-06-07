-- Market regime classifier (FEATURE_CHECKLIST Phase 2, Week 7, task 7.1).
--
-- One row per 5-minute snapshot of "what the market is doing" — the context
-- every alert is enriched with from Phase 2 on. `market/regime_job.py` upserts
-- this every 5 minutes during market hours; the confidence scorer (task 7.5)
-- and the morning brief (Week 9) read the latest row.
--
-- `as_of` is an ISO-8601 IST string (same convention as indicator_live.updated_at
-- and the signals tables) so rows are human-readable in the alert path.

CREATE TABLE IF NOT EXISTS market_state (
    as_of                 TEXT PRIMARY KEY,  -- ISO-8601 IST snapshot time
    nifty_direction       TEXT,              -- 'up' / 'flat' / 'down'
    nifty_return_pct      REAL,              -- NIFTY 50 pct_change for the session
    vix_level             REAL,              -- INDIA VIX last
    vix_state             TEXT,              -- 'low'/'normal'/'elevated'/'high'/'extreme'
    vix_direction         TEXT,              -- 'falling' / 'flat' / 'rising' (vs ~30m ago)
    gift_nifty_signal     TEXT,              -- 'aligned_bull'/'neutral'/'aligned_bear'
    advance_decline_ratio REAL,              -- advances / declines
    pct_above_vwap        REAL,              -- % of indicator_live symbols above VWAP
    fii_partial_day       REAL,              -- today's partial FII net flow (when available)
    overall_regime        TEXT,              -- 'risk_on'/'neutral'/'risk_off'/'panic'
    regime_confidence     REAL,              -- 0..1, how strongly the factors agree
    updated_at            TEXT               -- ISO-8601 IST write time
);

CREATE INDEX IF NOT EXISTS idx_market_state_asof ON market_state(as_of DESC);
