-- Layer 2 / Phase 7: raw_fundamentals_screener
-- Weekly fundamentals scraped from screener.in/company/<SYMBOL>/ for the
-- watchlist (config/universe.yaml `watchlist`). Feeds §5 Fundamentals
-- (fundamentals/from_screener.py): ROE, ROCE, 3y revenue/profit CAGR, etc.
--
-- External source (not NSE) fetched via httpx, outside the SessionManager.
-- robots.txt permits /company/<sym>/. Scraped politely: weekly, watchlist-only
-- (not the full universe), sequential with a small inter-request delay.
--
-- Snapshot keyed (symbol, as_of_date) = weekly capture date; re-runs upsert.
-- debt_to_equity is nullable — screener's default page only shows it for some
-- companies (it's not a default top-ratio); a balance-sheet-derived D/E is a
-- later enhancement.

CREATE TABLE IF NOT EXISTS raw_fundamentals_screener (
    symbol           TEXT NOT NULL,
    as_of_date       TEXT NOT NULL,     -- IST capture date, YYYY-MM-DD
    roce             REAL,              -- %
    roe              REAL,              -- %
    stock_pe         REAL,
    market_cap       REAL,              -- ₹ Cr
    book_value       REAL,
    dividend_yield   REAL,              -- %
    debt_to_equity   REAL,              -- NULL unless screener shows it
    sales_cagr_3y    REAL,              -- % (Compounded Sales Growth, 3 Years)
    profit_cagr_3y   REAL,              -- % (Compounded Profit Growth, 3 Years)
    source_url       TEXT,
    captured_at      INTEGER NOT NULL,
    PRIMARY KEY (symbol, as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_fund_screener_date ON raw_fundamentals_screener(as_of_date DESC);
