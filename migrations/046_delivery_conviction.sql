-- Delivery conviction (FEATURE_CHECKLIST Phase 4, Week 13, task 13.4).
--
-- Computed nightly from bhavcopy delivery data (indicators/delivery_tracker.py).
-- Delivery ratio = shares taken to demat / shares traded — high ratio means real
-- ownership change (accumulation/distribution) rather than intraday churn. The
-- composite score reads it together with price direction.

CREATE TABLE IF NOT EXISTS delivery_conviction (
    symbol                    TEXT NOT NULL,
    session_date              TEXT NOT NULL,
    delivery_ratio            REAL,       -- deliv_qty / traded_qty
    delivery_ratio_5d_avg     REAL,
    delivery_ratio_z_score    REAL,       -- vs 20d mean/std
    delivery_trend            TEXT,       -- 'rising' / 'flat' / 'falling'
    delivery_conviction_score REAL,
    PRIMARY KEY (symbol, session_date)
);
