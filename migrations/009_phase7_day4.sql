-- Layer 2 / Phase 7 Day 4 — three CsvCollectors + FII/DII (technically JSON)

-- ============================================================================
-- raw_bhavcopy_fo — F&O EOD bhavcopy, udiff format
-- ============================================================================
-- NSE migrated F&O bhavcopy in 2023 from fo<DDMMMYYYY>bhav.csv.zip to a new
-- "udiff" format at BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip.
-- 34 columns per row, ~45,000 contracts per trading day (futures + all
-- strike options across all expiries for all underlyings).
--
-- PK is (date + ticker + expiry + strike + option_type). Same PK as
-- architecture §7.1 except instrument_type collapsed into option_type=NULL
-- semantics for futures (CE/PE for options, NULL for futures).

CREATE TABLE IF NOT EXISTS raw_bhavcopy_fo (
    trade_date          TEXT NOT NULL,        -- ISO YYYY-MM-DD
    business_date       TEXT,                  -- usually same as trade_date
    segment             TEXT,                  -- 'FO' for all rows
    source              TEXT,                  -- 'NSE'
    instrument_type     TEXT,                  -- 'STO'=stock option, 'STF'=stock future, 'IDO'=index option, 'IDF'=index future
    instrument_id       TEXT,                  -- NSE's numeric ID per contract
    isin                TEXT,
    ticker              TEXT NOT NULL,         -- the underlying symbol (RELIANCE, NIFTY, etc.)
    series              TEXT,
    expiry              TEXT NOT NULL,         -- ISO YYYY-MM-DD
    actual_expiry       TEXT,                  -- if expiry was holiday-shifted
    strike              REAL NOT NULL,         -- 0 for futures
    option_type         TEXT NOT NULL,         -- 'CE'|'PE' for options; 'XX' for futures (NOT NULL because PK)
    instrument_name     TEXT,                  -- 'ABCAPITAL26MAY365CE' — NSE's display name
    open                REAL,
    high                REAL,
    low                 REAL,
    close               REAL,
    last                REAL,
    prev_close          REAL,
    underlying_price    REAL,
    settle              REAL,
    open_interest       INTEGER,
    change_in_oi        INTEGER,
    total_volume        INTEGER,
    total_value         REAL,
    total_trades        INTEGER,
    session_id          TEXT,                  -- 'F1' = regular session
    new_lot_size        INTEGER,
    PRIMARY KEY (trade_date, ticker, expiry, strike, option_type)
);
CREATE INDEX IF NOT EXISTS idx_bhav_fo_ticker_date ON raw_bhavcopy_fo(ticker, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_bhav_fo_date        ON raw_bhavcopy_fo(trade_date DESC);

-- ============================================================================
-- raw_volatility — CMVOLT historical volatility per F&O underlying
-- ============================================================================
-- One row per F&O underlying per trading day. Just annualized HV from
-- NSE's EWMA computation (sqrt(0.995*prev² + 0.005*today²) → annualized).
-- Architecture's "multi-horizon HV" is not what NSE serves — Layer 4 can
-- compute that from bhavcopy_cm if needed.
CREATE TABLE IF NOT EXISTS raw_volatility (
    date                       TEXT    NOT NULL,
    symbol                     TEXT    NOT NULL,
    underlying_close           REAL,
    underlying_prev_close      REAL,
    underlying_log_return      REAL,
    prev_day_volatility        REAL,
    daily_volatility           REAL,           -- column E in the CSV
    annualised_volatility      REAL,           -- column F — the headline number
    PRIMARY KEY (date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_volt_symbol_date ON raw_volatility(symbol, date DESC);

-- ============================================================================
-- raw_fii_dii — daily institutional cash-market flows
-- ============================================================================
-- One row per category per day. NSE serves only DII + FII (cash market net).
-- F&O institutional flow is in a different report we'll skip for now.
CREATE TABLE IF NOT EXISTS raw_fii_dii (
    date             TEXT    NOT NULL,
    category         TEXT    NOT NULL,         -- 'FII' | 'DII'
    buy_value        REAL,
    sell_value       REAL,
    net_value        REAL,
    fetched_at       INTEGER NOT NULL,
    PRIMARY KEY (date, category)
);
CREATE INDEX IF NOT EXISTS idx_fii_dii_date ON raw_fii_dii(date DESC);