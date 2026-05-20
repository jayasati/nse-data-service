-- Layer 2 / Phase 7 Day 5 — reference data tables (5 collectors)
-- All are slow-changing — universe, index membership, listings, fundamentals.

-- ============================================================================
-- raw_fno_list — F&O eligible securities (~209 stocks)
-- ============================================================================
-- ReferenceCollector with diff_upsert semantics. NSE's response IS today's
-- truth. When a stock joins or leaves F&O, diff catches it.
CREATE TABLE IF NOT EXISTS raw_fno_list (
    symbol             TEXT PRIMARY KEY,
    series             TEXT,
    last_price         REAL,
    fetched_at         INTEGER NOT NULL
);

-- ============================================================================
-- raw_index_members — which stocks belong to which index
-- ============================================================================
-- Fan-out per index. PK is (index_name, symbol). Same stock can appear in
-- multiple indices (e.g. RELIANCE is in NIFTY 50, NIFTY 100, NIFTY 500).
-- ReferenceCollector — refreshed weekly; diff_upsert tracks rebalances.
CREATE TABLE IF NOT EXISTS raw_index_members (
    index_name         TEXT NOT NULL,
    symbol             TEXT NOT NULL,
    series             TEXT,
    weightage_rank     INTEGER,            -- position in the index (1=biggest)
    last_price         REAL,
    fetched_at         INTEGER NOT NULL,
    PRIMARY KEY (index_name, symbol)
);
CREATE INDEX IF NOT EXISTS idx_idx_members_symbol ON raw_index_members(symbol);

-- ============================================================================
-- raw_new_listings — today's newly-listed stocks
-- ============================================================================
-- EventCollector pattern. Fingerprint = sha256(symbol + listing_date)[:16].
-- Stocks here auto-blacklist for 30 days per architecture §5.7 — new
-- listings are notoriously volatile in their first month.
CREATE TABLE IF NOT EXISTS raw_new_listings (
    fingerprint        TEXT PRIMARY KEY,
    symbol             TEXT NOT NULL,
    company_name       TEXT,
    series             TEXT,
    listing_date       TEXT,
    isin               TEXT,
    issue_price        REAL,
    market_lot         INTEGER,
    face_value         REAL,
    created_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nl_listing_date ON raw_new_listings(listing_date DESC);

-- ============================================================================
-- raw_primary_issues — upcoming IPO/OFS/rights/NCDs calendar
-- ============================================================================
-- One endpoint, four categories. We unify them with an issue_type tag.
-- EventCollector pattern — fingerprint = sha256(issue_type + symbol + open_date)[:16].
CREATE TABLE IF NOT EXISTS raw_primary_issues (
    fingerprint        TEXT PRIMARY KEY,
    issue_type         TEXT NOT NULL,        -- 'ipo' | 'ofs' | 'rights' | 'debt'
    symbol             TEXT,                  -- may be empty for NCDs
    company_name       TEXT,
    series             TEXT,
    issue_size         REAL,
    issue_price        REAL,
    price_band         TEXT,
    open_date          TEXT,
    close_date          TEXT,
    listing_date       TEXT,
    lot_size           INTEGER,
    status             TEXT,
    created_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_primary_open ON raw_primary_issues(open_date);
CREATE INDEX IF NOT EXISTS idx_primary_type ON raw_primary_issues(issue_type, open_date DESC);

-- ============================================================================
-- raw_quote_metadata — per-symbol fundamental + classification info
-- ============================================================================
-- Fan-out per watchlist + F&O symbol. Weekly refresh.
-- This populates the architecture's `fundamentals` table's first columns
-- (sector, industry, market_cap, P/E) — though those land in fundamentals
-- via a separate Layer 4 job, not here. This is the raw source.
CREATE TABLE IF NOT EXISTS raw_quote_metadata (
    symbol             TEXT PRIMARY KEY,
    company_name       TEXT,
    isin               TEXT,
    industry           TEXT,
    sector             TEXT,
    listing_date       TEXT,
    face_value         REAL,
    is_fno             INTEGER,             -- 0/1 boolean
    series             TEXT,
    trading_status     TEXT,
    last_price         REAL,
    pe_ratio           REAL,
    market_cap_cr      REAL,
    fetched_at         INTEGER NOT NULL
);