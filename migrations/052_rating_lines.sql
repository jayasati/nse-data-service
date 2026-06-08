-- Multi-instrument rating storage (Phase 5, Week 16 rework).
--
-- A rating filing is a TABLE of instruments (NCD/CP/FD/bank-loan…), sometimes
-- across multiple agencies, each with its own grade/outlook/action. We store:
--   raw_rating_lines     — one row per instrument (the full truth)
--   raw_rating_actions   — one derived headline per announcement (drives alerts)

CREATE TABLE IF NOT EXISTS raw_rating_lines (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    announcement_fingerprint TEXT NOT NULL,
    symbol                   TEXT NOT NULL,
    agency                   TEXT,
    instrument_type          TEXT,
    rated_amount             TEXT,
    lt_rating                TEXT,     -- long-term grade, e.g. 'AAA', 'BBB-'
    lt_outlook               TEXT,     -- 'Stable' / 'Negative' / 'Positive'
    st_rating                TEXT,     -- short-term, e.g. 'A1+'
    line_action              TEXT,     -- assigned/reaffirmed/outstanding/upgrade/downgrade
    broadcast_dt             TEXT
);

CREATE INDEX IF NOT EXISTS idx_rating_lines_fp ON raw_rating_lines(announcement_fingerprint);
CREATE INDEX IF NOT EXISTS idx_rating_lines_symbol ON raw_rating_lines(symbol);

-- Headline columns on the per-announcement table.
ALTER TABLE raw_rating_actions ADD COLUMN agencies TEXT;            -- comma-joined
ALTER TABLE raw_rating_actions ADD COLUMN n_instruments INTEGER;
ALTER TABLE raw_rating_actions ADD COLUMN worst_action TEXT;
ALTER TABLE raw_rating_actions ADD COLUMN min_lt_grade TEXT;
ALTER TABLE raw_rating_actions ADD COLUMN credit_quality_score REAL;  -- 0–100
ALTER TABLE raw_rating_actions ADD COLUMN outlook_negative INTEGER;
ALTER TABLE raw_rating_actions ADD COLUMN has_short_term INTEGER;
