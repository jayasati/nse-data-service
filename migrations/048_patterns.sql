-- Intraday pattern flags (FEATURE_CHECKLIST Phase 4, Week 15, tasks 15.1/15.2).
--
-- One row per (symbol, pattern, 5-min bar) when a pattern is detected by the
-- per-minute patterns job. Read by the dispatcher (divergence → confidence) and
-- rolled into stock_profile_daily nightly.

CREATE TABLE IF NOT EXISTS patterns (
    symbol       TEXT NOT NULL,
    pattern_type TEXT NOT NULL,    -- inside_bar | volume_dryup | near_support |
                                   -- near_resistance | higher_high | lower_low |
                                   -- bullish_divergence | bearish_divergence
    ts           INTEGER NOT NULL, -- 5-min bar start, UTC epoch
    session_date TEXT,             -- IST date, for daily rollups
    detail       REAL,             -- optional magnitude (e.g. distance %, volume ratio)
    detected_at  TEXT,
    PRIMARY KEY (symbol, pattern_type, ts)
);

CREATE INDEX IF NOT EXISTS idx_patterns_symbol_session
    ON patterns(symbol, session_date);
