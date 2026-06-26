-- P5 — historize the pre-market macro snapshot the morning brief already assembles live, so
-- "premarket macro → day outcome" becomes backtestable. Pragmatic subset (only fields we actually
-- collect — no NULL-bloat, no new feeds). One row per market morning.
CREATE TABLE IF NOT EXISTS premarket_snapshots (
    snapshot_date    TEXT PRIMARY KEY,
    captured_at      TEXT,
    gift_nifty_pct   REAL,
    gift_nifty_level REAL,
    gift_signal      TEXT,          -- GAP_UP | GAP_DOWN | FLAT
    brent            REAL,  brent_pct      REAL,
    gold             REAL,  gold_pct       REAL,
    usdinr           REAL,  usdinr_pct     REAL,
    copper           REAL,  copper_pct     REAL,
    aluminium        REAL,  aluminium_pct  REAL,
    sp500_pct        REAL,  nasdaq_pct     REAL,  dow_pct REAL,
    india_vix        REAL,  india_vix_signal TEXT,
    regime           TEXT,
    macro_bias       TEXT           -- BULLISH | BEARISH | MIXED | NEUTRAL
);
