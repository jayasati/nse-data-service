-- Layer 2 / Phase 6: raw_option_chain
-- Snapshot pattern. Each poll inserts one row per (symbol, expiry, strike,
-- option_type) at as_of. Re-polls accumulate, never overwrite. This gives
-- Layer 4 / Layer 6 the time series needed for OI velocity, IV changes,
-- max-pain drift, gamma squeeze detection.

CREATE TABLE IF NOT EXISTS raw_option_chain (
    symbol                    TEXT    NOT NULL,
    expiry                    TEXT    NOT NULL,        -- "26-May-2026" as NSE returns
    strike                    REAL    NOT NULL,
    option_type               TEXT    NOT NULL,        -- 'CE' or 'PE'
    as_of                     INTEGER NOT NULL,
    -- Spot
    underlying_value          REAL,
    -- OI
    open_interest             INTEGER,
    change_in_oi              INTEGER,
    pct_change_in_oi          REAL,
    -- Volume / value
    total_traded_volume       INTEGER,
    -- Price
    last_price                REAL,
    price_change              REAL,
    pct_price_change          REAL,
    implied_volatility        REAL,
    -- Order book (top of book only — NSE serves 5 depths but we keep just L1)
    total_buy_quantity        INTEGER,
    total_sell_quantity       INTEGER,
    bid_price                 REAL,
    bid_quantity              INTEGER,
    ask_price                 REAL,
    ask_quantity              INTEGER,
    -- NSE's unique identifier — useful as a stable key for deeper joins later
    identifier                TEXT,
    PRIMARY KEY (symbol, expiry, strike, option_type, as_of)
);

CREATE INDEX IF NOT EXISTS idx_oc_symbol_asof
    ON raw_option_chain(symbol, as_of DESC);

CREATE INDEX IF NOT EXISTS idx_oc_symbol_expiry_asof
    ON raw_option_chain(symbol, expiry, as_of DESC);