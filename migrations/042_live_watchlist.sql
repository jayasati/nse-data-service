-- Dynamic live watchlist (Phase 4 — focused universe).
--
-- The intraday compute used to sweep F&O ∪ Nifty500 (~750 names). Phase 4
-- narrows the live universe to a ~200-name core (top F&O by traded value) plus
-- this watchlist: names that earned live attention via a trigger (rating change,
-- exceptional news, OI spurt, 52-week-high breakout). Each row expires after a
-- few trading days unless re-triggered, so the live set stays small and current.

CREATE TABLE IF NOT EXISTS live_watchlist (
    symbol      TEXT PRIMARY KEY,
    reason      TEXT,        -- 'rating' | 'news' | 'oi_spurt' | 'breakout_52wh'
    added_at    TEXT,        -- ISO-8601 IST when first/last triggered
    expires_at  TEXT         -- ISO-8601 IST; refreshed on re-trigger
);

CREATE INDEX IF NOT EXISTS idx_live_watchlist_expiry ON live_watchlist(expires_at);
