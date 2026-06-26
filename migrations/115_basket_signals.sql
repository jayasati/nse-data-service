-- Task 2 — macro-theme / basket-rotation signals. One row per (basket, day): the moment a
-- basket's cross-sectional breadth + its macro driver align strongly enough to call a regime.
-- Consumed by: watchlist promotion (reason 'basket_rotation') + cause attribution (a member's
-- move is attributed to its basket, not 'unknown'). SWING horizon (1-5d), not intraday.
CREATE TABLE IF NOT EXISTS basket_signals (
    signal_date      TEXT NOT NULL,      -- IST trading date 'YYYY-MM-DD'
    basket_name      TEXT NOT NULL,
    ts               INTEGER,            -- epoch of first emission today
    breadth_score    REAL,               -- (advancing - declining) / member_count, [-1, 1]
    driver_name      TEXT,
    driver_move_pct  REAL,
    signal_type      TEXT,               -- 'BASKET_LONG' | 'BASKET_SHORT'
    member_count     INTEGER,
    advancing        INTEGER,
    declining        INTEGER,
    confidence       REAL,               -- min(1, |breadth| * |driver| / 2)
    PRIMARY KEY (signal_date, basket_name)   -- "not already active for this basket today"
);
