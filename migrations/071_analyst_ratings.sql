-- Analyst broker-recommendation actions (FEATURE_CHECKLIST Week 18, task 18.7).
--
-- Structured rows scraped from the Moneycontrol broker-recommendations feed by
-- parsers/analyst_ratings.py (30-min cadence, market hours). `fingerprint` is
-- UNIQUE so re-polls of the feed are idempotent. old_call/old_target are
-- derived from OUR OWN previous row for the same (symbol, brokerage) — the
-- feed only carries the new call — so the first sighting has them NULL and an
-- upgrade/downgrade can only be detected from the second sighting onward.
-- `tier` comes from config/brokerage_tiers.yaml (1 = Tier-1, 2 = Tier-2,
-- NULL = unranked); the analyst_upgrade_tier1 / analyst_downgrade_tier1
-- signals (task 18.8) fire on tier-1 call changes only.

CREATE TABLE IF NOT EXISTS raw_analyst_ratings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    brokerage     TEXT,
    tier          INTEGER,            -- 1 / 2 / NULL per brokerage_tiers.yaml
    old_call      TEXT,               -- from our previous row, NULL on first sight
    new_call      TEXT,               -- 'buy'/'accumulate'/'hold'/'reduce'/'sell'/...
    old_target    REAL,
    new_target    REAL,
    published_at  TEXT,               -- feed pubDate, RFC-822 as published
    source_url    TEXT,
    fingerprint   TEXT UNIQUE,        -- hash of the feed item; re-poll idempotency
    created_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analyst_ratings_symbol
    ON raw_analyst_ratings(symbol, id DESC);
