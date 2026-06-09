-- Analyst consensus estimates (Phase 5 — Event Intelligence, E4).
--
-- Optional enrichment layer: when a consensus revenue / PAT / EPS estimate is
-- available for an upcoming quarter, the earnings engine computes a TRUE
-- beat/miss (actual vs estimate) instead of relying only on the YoY-trend proxy.
-- The whole engine works without this table; rows just make the surprise sharper.
--
-- Source-agnostic: `source` tags where an estimate came from (a scraper, a
-- manual CSV, an API). One row per (symbol, period_ending, source); the latest
-- by as_of wins when several sources disagree.

CREATE TABLE IF NOT EXISTS consensus_estimates (
    symbol         TEXT NOT NULL,
    period_ending  TEXT NOT NULL,      -- 'YYYY-MM-DD' quarter the estimate is for
    rev_est_cr     REAL,               -- consensus revenue (crore)
    pat_est_cr     REAL,               -- consensus PAT (crore)
    eps_est        REAL,               -- consensus EPS (rupees/share)
    source         TEXT NOT NULL,      -- 'moneycontrol' | 'manual' | ...
    as_of          INTEGER NOT NULL,   -- when this estimate was captured (epoch)
    PRIMARY KEY (symbol, period_ending, source)
);

CREATE INDEX IF NOT EXISTS idx_consensus_symbol
    ON consensus_estimates(symbol, period_ending);
