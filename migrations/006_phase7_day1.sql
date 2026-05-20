-- Layer 2 / Phase 7 Day 1 — five SnapshotCollectors
-- All follow the (PK includes as_of) pattern: re-polls accumulate.

-- ============================================================================
-- raw_equity_quotes — live LTPs from /api/equity-stockIndices?index=...
-- ============================================================================
-- One row per (symbol, as_of). The `index` column says which list this came
-- from (NIFTY 50, NIFTY 500, etc.) — supports the same symbol being polled
-- under multiple indices without PK collision (use index as part of meta).
CREATE TABLE IF NOT EXISTS raw_equity_quotes (
    symbol            TEXT    NOT NULL,
    as_of             INTEGER NOT NULL,
    index_name        TEXT    NOT NULL,   -- which list this came from
    last_price        REAL,
    open             REAL,
    day_high         REAL,
    day_low          REAL,
    prev_close       REAL,
    change           REAL,
    pct_change       REAL,
    volume           INTEGER,
    value            REAL,
    year_high        REAL,
    year_low         REAL,
    PRIMARY KEY (symbol, index_name, as_of)
);
CREATE INDEX IF NOT EXISTS idx_eq_quotes_asof ON raw_equity_quotes(as_of DESC);
CREATE INDEX IF NOT EXISTS idx_eq_quotes_sym  ON raw_equity_quotes(symbol, as_of DESC);

-- ============================================================================
-- raw_indices — 139 indices from /api/allIndices
-- ============================================================================
CREATE TABLE IF NOT EXISTS raw_indices (
    index_symbol     TEXT    NOT NULL,
    as_of            INTEGER NOT NULL,
    index_name       TEXT,
    last             REAL,
    variation        REAL,
    pct_change       REAL,
    open             REAL,
    high             REAL,
    low              REAL,
    prev_close       REAL,
    PRIMARY KEY (index_symbol, as_of)
);
CREATE INDEX IF NOT EXISTS idx_indices_asof ON raw_indices(as_of DESC);

-- ============================================================================
-- raw_advances_declines — market breadth, derived from /api/allIndices
-- ============================================================================
-- One row per poll. Total counts across all listed securities.
CREATE TABLE IF NOT EXISTS raw_advances_declines (
    as_of            INTEGER PRIMARY KEY,
    advances         INTEGER,
    declines         INTEGER,
    unchanged        INTEGER
);

-- ============================================================================
-- raw_market_movers — gainers and losers, with category
-- ============================================================================
-- A "mover" is a stock currently up or down significantly. NSE returns 7
-- categories per call (NIFTY, BANKNIFTY, NIFTYNEXT50, SecGtr20, SecLwr20,
-- FOSec, allSec). We tag each row with category so Layer 6 can filter.
CREATE TABLE IF NOT EXISTS raw_market_movers (
    symbol           TEXT    NOT NULL,
    as_of            INTEGER NOT NULL,
    direction        TEXT    NOT NULL,   -- 'gainer' | 'loser'
    category         TEXT    NOT NULL,   -- 'NIFTY' | 'BANKNIFTY' | 'FOSec' | etc.
    last_price       REAL,
    open             REAL,
    day_high         REAL,
    day_low          REAL,
    prev_close       REAL,
    pct_change       REAL,
    volume           INTEGER,
    value            REAL,
    PRIMARY KEY (symbol, direction, category, as_of)
);
CREATE INDEX IF NOT EXISTS idx_movers_asof_dir
    ON raw_market_movers(as_of DESC, direction);

-- ============================================================================
-- raw_most_active — turnover and volume leaders
-- ============================================================================
-- NSE has two list types: by volume (most-traded shares) and by value
-- (highest turnover). Both get tagged via list_type.
CREATE TABLE IF NOT EXISTS raw_most_active (
    symbol            TEXT    NOT NULL,
    as_of             INTEGER NOT NULL,
    list_type         TEXT    NOT NULL,   -- 'volume' | 'value'
    rank              INTEGER,
    last_price        REAL,
    pct_change        REAL,
    quantity_traded   INTEGER,
    total_volume      INTEGER,
    total_value       REAL,
    prev_close        REAL,
    PRIMARY KEY (symbol, list_type, as_of)
);
CREATE INDEX IF NOT EXISTS idx_most_active_asof_type
    ON raw_most_active(as_of DESC, list_type);