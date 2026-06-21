-- Weekly positional ranking snapshots — one row per (week, symbol). History accumulates so the
-- UI can scroll back through previous weeks: base rank + factor scores + slow-data overlay flags
-- + ATR-based sizing for the selected basket.
CREATE TABLE IF NOT EXISTS weekly_ranking (
    week_date            TEXT NOT NULL,     -- snapshot date (the Monday run)
    symbol               TEXT NOT NULL,
    sector               TEXT,
    grade                TEXT,
    base_rank            INTEGER,           -- rank by lean_score (pre-overlay)
    lean_score           REAL,
    valuation            REAL,
    surprise             REAL,
    risk                 REAL,
    adj_score            REAL,              -- lean_score after the slow-data overlay
    -- slow-data overlay
    pledge_pct           REAL,
    pledge_delta         REAL,              -- QoQ change (rising = red flag)
    fii_pct              REAL,
    fii_delta            REAL,              -- QoQ change (rising = accumulation)
    delivery_conviction  REAL,
    block_net_cr         REAL,
    credit_action        TEXT,
    flags                TEXT,              -- JSON list of human flags
    excluded             INTEGER DEFAULT 0, -- 1 = hard-excluded (risk/junk/pledge)
    -- ATR sizing (selected basket only)
    atr_pct              REAL,
    entry_px             REAL,
    stop_px              REAL,
    qty                  INTEGER,
    risk_rupees          REAL,
    selected             INTEGER DEFAULT 0, -- 1 = in the final sized basket
    final_rank           INTEGER,
    created_at           INTEGER,
    PRIMARY KEY (week_date, symbol)
);
CREATE INDEX IF NOT EXISTS ix_weekly_ranking_week ON weekly_ranking(week_date);
