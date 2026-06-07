-- Credit-rating actions (FEATURE_CHECKLIST Phase 5, Week 16, task 16.6).
--
-- Structured rows parsed from credit-rating announcement PDFs by
-- parsers/rating_extractor.py. `announcement_fingerprint` is UNIQUE so a
-- re-run / backfill is idempotent, and feeds the credit_* signals.

CREATE TABLE IF NOT EXISTS raw_rating_actions (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol                   TEXT NOT NULL,
    agency                   TEXT,
    action                   TEXT,    -- upgrade/downgrade/reaffirm/watch_negative/assigned
    old_rating               TEXT,
    new_rating               TEXT,
    instrument_type          TEXT,
    is_junk_downgrade        INTEGER DEFAULT 0,
    broadcast_dt             TEXT,
    announcement_fingerprint TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_rating_actions_symbol ON raw_rating_actions(symbol);
