-- Layer 2 / Phase 7: raw_gift_nifty
-- GIFT Nifty (NSE International Exchange) — the pre-open predictor of the NSE
-- cash open (research Pillar 2). Polled every 30s during 06:30–09:15 IST.
--
-- Source: https://www.nseix.com/api/nifty-market-rate (NSE IX, REST JSON, no
-- token needed). External — fetched via httpx, outside the NSE SessionManager.
--
-- Each 30s poll is a snapshot keyed (index_name, as_of) where as_of is the
-- capture unix time, so the morning window accumulates a tick series. CURRVALUE
-- arrives comma-formatted ("23,913.70") and is parsed to REAL.

CREATE TABLE IF NOT EXISTS raw_gift_nifty (
    index_name     TEXT NOT NULL,    -- 'Nifty 50' (GIFT Nifty tracks Nifty 50)
    as_of          INTEGER NOT NULL, -- capture time (unix seconds)
    curr_value     REAL,             -- CURRVALUE
    open_value     REAL,             -- OI_OPEN_INDEX_VAL
    close_value    REAL,             -- OI_CLOSE_INDEX_VAL (prev close)
    change         REAL,
    pct_change     REAL,
    nse_timestamp  TEXT,             -- FULLTIMESTAMP, verbatim
    captured_at    INTEGER NOT NULL,
    PRIMARY KEY (index_name, as_of)
);

CREATE INDEX IF NOT EXISTS idx_gift_nifty_asof ON raw_gift_nifty(as_of DESC);
