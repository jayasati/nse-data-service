-- Layer 2 / Phase 7: raw_macro  (external macro sources via Yahoo Finance)
-- Daily snapshot of the global macro backdrop for the macro_regime feature:
-- USDINR, Brent crude, Gold, and the US indices (S&P 500 / Nasdaq / Dow).
--
-- Source is Yahoo's chart API (query1.finance.yahoo.com/v8/finance/chart/<sym>),
-- fetched with httpx directly — this collector lives OUTSIDE the NSE
-- SessionManager (Yahoo isn't NSE; no cookie warm-up / circuit needed). It is
-- intentionally NOT gated on the NSE trading calendar: global markets move on
-- Indian holidays, which is exactly when the macro signal matters.
--
-- Keyed on (asset, as_of_date) = IST capture date, so the daily 18:00 run
-- upserts one row per asset per day. market_time records the underlying
-- quote's freshness (US closes lag IST, so at 18:00 IST the US indices carry
-- the prior US session's close — expected).

CREATE TABLE IF NOT EXISTS raw_macro (
    asset         TEXT NOT NULL,    -- USDINR | BRENT | GOLD | SP500 | NASDAQ | DOW
    as_of_date    TEXT NOT NULL,    -- IST capture date, YYYY-MM-DD
    yahoo_symbol  TEXT,
    price         REAL,             -- regularMarketPrice
    prev_close    REAL,             -- chartPreviousClose
    change        REAL,
    pct_change    REAL,
    day_high      REAL,
    day_low       REAL,
    volume        INTEGER,
    currency      TEXT,
    market_time   INTEGER,          -- regularMarketTime (unix) — quote freshness
    captured_at   INTEGER NOT NULL,
    PRIMARY KEY (asset, as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_macro_date ON raw_macro(as_of_date DESC);
