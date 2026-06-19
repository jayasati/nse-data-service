-- Daily macro market levels for the Macro Shock engine (currency + oil shock).
-- Populated by scripts/load_macro_market.py from yfinance (USDINR=INR=X, Brent=BZ=F).
CREATE TABLE IF NOT EXISTS raw_macro_market (
    date        TEXT PRIMARY KEY,       -- 'YYYY-MM-DD'
    usdinr      REAL,
    brent       REAL,
    fetched_at  INTEGER
);
