-- Layer 2 / Phase 7: raw_pre_open
-- Pre-open session snapshot (09:00–09:15 IST). Fired once at ~09:08 IST after
-- the pre-open book has settled but before continuous trading begins.
--
-- NSE's /api/market-data-pre-open?key=ALL returns one entry per security with
-- a `metadata` block (headline IEP / change / prev close) and a
-- `detail.preOpenMarket` block (the order-book aggregates + ATO quantities).
--
-- The gap-detection signal is IEP vs prev_close (already surfaced as
-- change / pct_change). Each poll is a point-in-time snapshot keyed by
-- (symbol, as_of), so a same-session re-poll is idempotent and re-polling on
-- later days accumulates rows — same semantics as raw_oi_spurts.
--
-- Per-symbol grain only. The response's market-wide breadth (advances /
-- declines / unchanged) is a different grain and is not stored here.
-- marketCap is intentionally dropped — this feed always returns "-" for it;
-- market cap lives in raw_quote_metadata.

CREATE TABLE IF NOT EXISTS raw_pre_open (
    symbol               TEXT NOT NULL,
    as_of                INTEGER NOT NULL,   -- capture time (unix seconds)
    series               TEXT,               -- EQ / BE / SM / ST / BZ / IV
    iep                  REAL,               -- indicative equilibrium price
    final_price          REAL,               -- preOpenMarket.finalPrice
    prev_close           REAL,
    change               REAL,               -- iep - prev_close
    pct_change           REAL,
    final_quantity       INTEGER,
    total_traded_volume  INTEGER,
    total_turnover       REAL,
    total_buy_qty        INTEGER,            -- preOpenMarket.totalBuyQuantity
    total_sell_qty       INTEGER,            -- preOpenMarket.totalSellQuantity
    ato_buy_qty          INTEGER,            -- at-the-open buy quantity
    ato_sell_qty         INTEGER,
    year_high            REAL,
    year_low             REAL,
    nse_timestamp        TEXT,               -- response-level timestamp, verbatim
    PRIMARY KEY (symbol, as_of)
);

CREATE INDEX IF NOT EXISTS idx_pre_open_asof
    ON raw_pre_open(as_of DESC);

CREATE INDEX IF NOT EXISTS idx_pre_open_symbol_asof
    ON raw_pre_open(symbol, as_of DESC);
