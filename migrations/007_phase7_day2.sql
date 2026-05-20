-- Layer 2 / Phase 7 Day 2 — three SnapshotCollectors

-- ============================================================================
-- raw_high_low_52w — stocks at 52-week high or low
-- ============================================================================
-- NSE's payload has dataLtpGreater20 (price > ₹20) and dataLtpLess20 (penny
-- stocks) — we union them with a price_tier tag. event = 'high' | 'low'.
CREATE TABLE IF NOT EXISTS raw_high_low_52w (
    symbol           TEXT    NOT NULL,
    as_of            INTEGER NOT NULL,
    event            TEXT    NOT NULL,   -- 'high' | 'low'
    price_tier       TEXT    NOT NULL,   -- 'gt20' | 'lte20'
    company_name     TEXT,
    new_52w_level    REAL,               -- the new 52w high or low price
    prev_52w_level   REAL,               -- previous 52w extreme being broken
    prev_hl_date     TEXT,               -- when the prev extreme was set
    ltp              REAL,
    prev_close       REAL,
    change           REAL,
    pct_change       REAL,
    PRIMARY KEY (symbol, event, price_tier, as_of)
);
CREATE INDEX IF NOT EXISTS idx_52w_asof
    ON raw_high_low_52w(as_of DESC);
CREATE INDEX IF NOT EXISTS idx_52w_event
    ON raw_high_low_52w(event, as_of DESC);

-- ============================================================================
-- raw_band_hits — stocks hitting upper or lower circuit
-- ============================================================================
-- NSE returns three categories: AllSec, SecGtr20, SecLwr20.
-- AllSec is the union; storing all three lets Layer 6 filter by tier.
CREATE TABLE IF NOT EXISTS raw_band_hits (
    symbol           TEXT    NOT NULL,
    as_of            INTEGER NOT NULL,
    band             TEXT    NOT NULL,   -- 'upper' | 'lower'
    category         TEXT    NOT NULL,   -- 'AllSec' | 'SecGtr20' | 'SecLwr20'
    series           TEXT,
    ltp              REAL,
    band_pct         REAL,               -- the % band, e.g. 5/10/20
    open             REAL,
    high             REAL,
    low              REAL,
    prev_close       REAL,
    PRIMARY KEY (symbol, band, category, as_of)
);
CREATE INDEX IF NOT EXISTS idx_band_asof
    ON raw_band_hits(as_of DESC);

-- ============================================================================
-- raw_most_active_fno — most active F&O contracts by volume / value
-- ============================================================================
-- Architecture §5.2 #18-19. Per-contract granularity (so includes expiry +
-- strike + option type when applicable; just symbol for futures).
CREATE TABLE IF NOT EXISTS raw_most_active_fno (
    symbol           TEXT    NOT NULL,
    as_of            INTEGER NOT NULL,
    list_type        TEXT    NOT NULL,   -- 'volume' | 'value'
    rank             INTEGER,
    instrument       TEXT,               -- 'FUTSTK' | 'OPTSTK' | 'FUTIDX' | 'OPTIDX'
    expiry           TEXT,
    strike           REAL,
    option_type      TEXT,               -- 'CE' | 'PE' | NULL for futures
    last_price       REAL,
    pct_change       REAL,
    contracts_traded INTEGER,
    value_lacs       REAL,
    open_interest    INTEGER,
    PRIMARY KEY (symbol, list_type, as_of, rank)
);
CREATE INDEX IF NOT EXISTS idx_fno_active_asof
    ON raw_most_active_fno(as_of DESC, list_type);