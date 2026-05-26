-- Layer 2 / Phase 7: raw_call_auction
-- Securities currently traded via the periodic call auction for illiquid
-- securities (NSE "Stocks in Call Auction"). The membership of this set is
-- the signal Layer 6 wants: a stock here is illiquid and should be excluded
-- from intraday signals.
--
-- Source: /api/live-watch-call-auction -> data[] (one row per security, with
-- per-session price/qty for the day's six call-auction windows + day totals).
--
-- ReferenceCollector semantics (diff_upsert, key = symbol): the current
-- response IS the truth. A symbol entering the set -> inserted; leaving ->
-- removed (deleted). That add/remove signal is exactly the "started/stopped
-- being illiquid" event Layer 6 cares about. Pulled once daily after the
-- day's sessions close (status CLOSED), so the row holds the day's final
-- auction numbers.
--
-- captured_at follows the surveillance convention (raw_surveillance_*.fetched_at):
-- our capture time, present so ops can see freshness.

CREATE TABLE IF NOT EXISTS raw_call_auction (
    symbol          TEXT PRIMARY KEY,
    avg_price       REAL,
    total_volume    INTEGER,
    total_turnover  REAL,
    session1_price  REAL,
    session1_qty    INTEGER,
    session2_price  REAL,
    session2_qty    INTEGER,
    session3_price  REAL,
    session3_qty    INTEGER,
    session4_price  REAL,
    session4_qty    INTEGER,
    session5_price  REAL,
    session5_qty    INTEGER,
    session6_price  REAL,
    session6_qty    INTEGER,
    captured_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_call_auction_captured
    ON raw_call_auction(captured_at);
