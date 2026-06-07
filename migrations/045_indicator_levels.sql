-- Per-symbol price levels (FEATURE_CHECKLIST Phase 4, Week 13, task 13.1).
--
-- Computed nightly from bhavcopy (indicators/levels.py) and loaded into Redis
-- at 08:45 — static reference levels for the session: prior-day high/low, 52w
-- extremes, recent ranges, the nearest psychological round number (and how often
-- price has failed there), and classic floor pivots from the prior day.

CREATE TABLE IF NOT EXISTS indicator_levels (
    symbol                     TEXT NOT NULL,
    session_date               TEXT NOT NULL,
    high_52w                   REAL,
    low_52w                    REAL,
    days_since_52w_high        INTEGER,
    days_since_52w_low         INTEGER,
    pdh                        REAL,      -- prior day high
    pdl                        REAL,      -- prior day low
    range_5d_high              REAL,
    range_5d_low               REAL,
    range_20d_high             REAL,
    range_20d_low              REAL,
    nearest_round_number       REAL,
    dist_from_round_pct        REAL,
    round_number_prior_failures INTEGER,  -- approaches within 0.5% that failed to break
    r1                         REAL,
    r2                         REAL,
    s1                         REAL,
    s2                         REAL,      -- pivots from prior-day H/L/C
    PRIMARY KEY (symbol, session_date)
);
