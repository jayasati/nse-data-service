-- P4 multi-factor paper-trade log. One row per aggressive intraday move on a
-- tracked A/B name (from intraday_move_events), stamped with its PRIMARY internal
-- catalyst bucket (so we can segment forward edge BY CAUSE TYPE — order-win vs
-- rating-change vs block-deal vs earnings, the user's thesis) and the LLM "why"
-- from move_causes when present. The labeler fills net-of-cost forward returns.
--
-- This is a forward RESEARCH log, not a trading system: no order is placed,
-- "entry" is the move-day close (follow-through convention, matches
-- backtest_move_causes.py). See plans/P4_PAPER_TRADE_PLAN.md.
--
-- OOS discipline: batch='forward' rows are the CERTIFYING set (the honest
-- out-of-sample stream that breaks P1's power deadlock). batch='backfill' rows
-- (seeded from history) are colour/debug ONLY and must never enter the promote
-- gate, because they overlap the historical drift test we already killed.

CREATE TABLE IF NOT EXISTS paper_observations (
    id            INTEGER PRIMARY KEY,
    symbol        TEXT NOT NULL,
    move_date     TEXT NOT NULL,            -- date of the aggressive move (IST)
    batch         TEXT NOT NULL DEFAULT 'forward',   -- 'forward' | 'backfill'
    captured_at   TEXT NOT NULL DEFAULT (datetime('now')),
    grade         TEXT,                     -- A core / B tradeable (universe grade at capture)
    sector        TEXT,                     -- sector class (for benchmark ETF)
    direction     TEXT,                     -- 'up' (long) / 'down' (short)
    move_pct      REAL,                     -- the dominant leg % that triggered capture
    entry_price   REAL,                     -- move-day close (notional mark; no look-ahead)

    -- cause attribution --------------------------------------------------
    catalyst      TEXT,                     -- PRIMARY internal bucket; segmentation key
                                            -- (e.g. ann:result, ann:other, block_deal,
                                            --  rating, corp_action, oi_spurt, none)
    cause_category TEXT,                    -- LLM-fused category from move_causes (nullable)
    cause_summary  TEXT,                    -- the human-readable "why" (nullable)
    idiosyncratic  INTEGER,                 -- 1 = stock-specific, 0 = beta/systematic
    source_url     TEXT,
    source_date    TEXT,

    -- outcome (filled by label_paper_observations.py) --------------------
    bench_symbol  TEXT,                     -- benchmark ETF used
    ret_1d  REAL, ret_3d  REAL, ret_5d  REAL, ret_10d  REAL,   -- raw stock return %
    excess_1d REAL, excess_3d REAL, excess_5d REAL, excess_10d REAL,  -- vs benchmark
    net_1d  REAL, net_3d  REAL, net_5d  REAL, net_10d  REAL,   -- excess x dir - cost
    cost_pct REAL,                          -- round-trip cost applied to net_*
    labeled_at TEXT,
    status   TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'labeled'

    UNIQUE (symbol, move_date)
);

CREATE INDEX IF NOT EXISTS ix_paper_obs_status ON paper_observations(status);
CREATE INDEX IF NOT EXISTS ix_paper_obs_catalyst ON paper_observations(catalyst, batch);
