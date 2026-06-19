-- RBI press releases (rbi.org.in) — central-bank policy/regulatory events that move
-- banks & rate-sensitive sectors but aren't per-stock NSE filings (so the News engine
-- can't see them). Populated by scripts/load_rbi_press.py; surfaced as a sector catalyst.
CREATE TABLE IF NOT EXISTS raw_rbi_press (
    prid        INTEGER PRIMARY KEY,      -- RBI press-release id (monotonic in time)
    pub_date    TEXT,                     -- 'YYYY-MM-DD'
    title       TEXT,
    kind        TEXT,                     -- monetary_policy | banking_regulatory | liquidity | other
    url         TEXT,
    first_seen  INTEGER
);
CREATE INDEX IF NOT EXISTS ix_rbi_press_date ON raw_rbi_press (pub_date);
