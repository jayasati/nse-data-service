-- Paper-track review snapshots — per-strategy realized stats, recorded weekly. The job only
-- ALERTS when a strategy crosses the maturity bar (>=20 closed real trades) so we judge edge on
-- a real sample, not eyeball noise. This table keeps the history for trend.
CREATE TABLE IF NOT EXISTS paper_review (
    review_date   TEXT NOT NULL,
    strategy      TEXT NOT NULL,
    n_closed      INTEGER,        -- real closed trades (same-day 'dropped' non-trades excluded)
    n_open        INTEGER,
    win_pct       REAL,
    avg_net_pct   REAL,
    median_net_pct REAL,
    profit_factor REAL,
    total_pnl     REAL,
    mature        INTEGER,        -- 1 if n_closed >= maturity bar
    created_at    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (review_date, strategy)
);
