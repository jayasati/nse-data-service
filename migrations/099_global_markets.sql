-- Global / cross-asset market context (Phase 1) — US indices, VIX, DXY, US yields, crude, gold,
-- Asia, and FRESH Indian indices (from Yahoo, bypassing the stale broker candle feed). Snapshot
-- per fetch so the desk report's risk-on/off + gap bias is data-backed, not a guess.
CREATE TABLE IF NOT EXISTS raw_global_markets (
    ticker       TEXT NOT NULL,     -- ^GSPC, ^VIX, CL=F, ^NSEI, …
    name         TEXT,
    asset_class  TEXT,              -- india_index | us_index | vol | rate | fx | commodity | asia
    as_of        INTEGER NOT NULL,
    last         REAL,
    prev_close   REAL,
    change       REAL,
    pct_change   REAL,
    captured_at  INTEGER,
    PRIMARY KEY (ticker, as_of)
);
CREATE INDEX IF NOT EXISTS ix_global_markets_asof ON raw_global_markets(as_of);
