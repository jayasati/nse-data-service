-- Layer 2 / Phase 7 Day 3 — five EventCollectors (Archetype B, fingerprint dedup)

-- ============================================================================
-- raw_board_meetings — upcoming corporate board meetings
-- ============================================================================
-- These are scheduled events, announced ahead of time. The same meeting may
-- appear multiple polls; dedup on fingerprint(symbol + bm_date + purpose).
-- Used by Layer 5/6 events.calendar — pre-screening before earnings releases.
CREATE TABLE IF NOT EXISTS raw_board_meetings (
    fingerprint        TEXT PRIMARY KEY,
    symbol             TEXT NOT NULL,
    meeting_date       TEXT NOT NULL,        -- when the meeting will occur (NSE text format)
    purpose            TEXT,
    details            TEXT,
    industry           TEXT,
    company_name       TEXT,
    isin               TEXT,
    timestamp          TEXT,                  -- when NSE published this notification
    attachment_url     TEXT,
    created_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bm_date   ON raw_board_meetings(meeting_date);
CREATE INDEX IF NOT EXISTS idx_bm_symbol ON raw_board_meetings(symbol, meeting_date DESC);

-- ============================================================================
-- raw_corporate_actions — dividends, splits, bonuses, rights
-- ============================================================================
-- Architecture §7.1 base. We dedup on (symbol + subject + ex_date) because
-- the same dividend action may be reported multiple times.
CREATE TABLE IF NOT EXISTS raw_corporate_actions (
    fingerprint        TEXT PRIMARY KEY,
    symbol             TEXT NOT NULL,
    series             TEXT,
    industry           TEXT,
    face_value         REAL,
    subject            TEXT,                  -- "Interim Dividend - Rs 11.70 Per Share"
    ex_date            TEXT,
    record_date        TEXT,
    bc_start_date      TEXT,                  -- book closure window
    bc_end_date        TEXT,
    nd_start_date      TEXT,                  -- no-delivery window
    nd_end_date        TEXT,
    ca_broadcast_date  TEXT,
    company_name       TEXT,
    isin               TEXT,
    created_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ca_symbol  ON raw_corporate_actions(symbol, ex_date DESC);
CREATE INDEX IF NOT EXISTS idx_ca_ex_date ON raw_corporate_actions(ex_date);

-- ============================================================================
-- raw_financial_results — quarterly result filings
-- ============================================================================
-- NSE serves the full archive in one call (~3,800 rows). Fingerprint on
-- (symbol + period + relatingTo + filingDate) — same filing can appear in
-- multiple poll responses if it gets re-broadcast.
CREATE TABLE IF NOT EXISTS raw_financial_results (
    fingerprint        TEXT PRIMARY KEY,
    symbol             TEXT NOT NULL,
    company_name       TEXT,
    industry           TEXT,
    period             TEXT,                  -- 'Quarterly' | 'Annual' | etc.
    relating_to        TEXT,                  -- 'First Quarter', 'Third Quarter', etc.
    financial_year     TEXT,
    from_date          TEXT,
    to_date            TEXT,
    audited            TEXT,                  -- 'Audited' | 'Un-Audited'
    cumulative         TEXT,
    ind_as             TEXT,
    re_ind             TEXT,
    bank               TEXT,                  -- 'Y' | 'N' (banking sector flag)
    filing_date        TEXT,
    seq_number         TEXT,
    old_new_flag       TEXT,
    xbrl_url           TEXT,
    result_url         TEXT,
    params             TEXT,                  -- NSE's internal lookup string
    format             TEXT,
    created_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fr_symbol      ON raw_financial_results(symbol, filing_date DESC);
CREATE INDEX IF NOT EXISTS idx_fr_filing_date ON raw_financial_results(filing_date DESC);

-- ============================================================================
-- raw_large_deals — bulk, block, and short deals (live snapshot)
-- ============================================================================
-- One endpoint returns three flavors of deals in one response. We unify them
-- with a deal_type tag. Used by Layer 6 institutional-flow signals.
CREATE TABLE IF NOT EXISTS raw_large_deals (
    fingerprint        TEXT PRIMARY KEY,
    deal_type          TEXT NOT NULL,         -- 'bulk' | 'block' | 'short'
    deal_date          TEXT,                  -- text format from NSE
    symbol             TEXT NOT NULL,
    company_name       TEXT,
    client_name        TEXT,
    buy_sell           TEXT,                  -- 'BUY' | 'SELL'
    quantity           INTEGER,
    weighted_avg_price REAL,                  -- watp field
    remarks            TEXT,
    created_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ld_symbol ON raw_large_deals(symbol, deal_date DESC);
CREATE INDEX IF NOT EXISTS idx_ld_type   ON raw_large_deals(deal_type, deal_date DESC);

-- ============================================================================
-- raw_insider_trading — promoter / insider buy-sell filings (SEBI PIT)
-- ============================================================================
-- /api/corporates-pit. Returns empty payload most days; the moment a real
-- filing happens, dedup-by-fingerprint catches it. Architecture §5.4 #35.
CREATE TABLE IF NOT EXISTS raw_insider_trading (
    fingerprint        TEXT PRIMARY KEY,
    symbol             TEXT NOT NULL,
    company_name       TEXT,
    acquirer_name      TEXT,
    acquirer_category  TEXT,                  -- 'Promoter' | 'Director' | 'KMP' | etc.
    securities_type    TEXT,
    transaction_type   TEXT,                  -- 'Buy' | 'Sell' | 'Pledge' | etc.
    no_of_securities   INTEGER,
    value_in_rupees    REAL,
    holding_before     INTEGER,
    holding_after      INTEGER,
    period_from        TEXT,
    period_to          TEXT,
    intimation_date    TEXT,
    mode_of_acquisition TEXT,
    derivative_contract TEXT,
    attachment_url     TEXT,
    created_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pit_symbol ON raw_insider_trading(symbol, intimation_date DESC);
CREATE INDEX IF NOT EXISTS idx_pit_acq    ON raw_insider_trading(acquirer_name);