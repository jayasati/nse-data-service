-- Options analytics: GEX / max-pain / PCR (FEATURE_CHECKLIST Week 23).
-- Per-symbol-per-expiry snapshot + the index GEX mirrored onto market_state (task 23.3).
ALTER TABLE market_state ADD COLUMN gex_total REAL;
ALTER TABLE market_state ADD COLUMN gex_sign TEXT;        -- positive=mean-revert · negative=trending
ALTER TABLE market_state ADD COLUMN gex_flip_level REAL;

CREATE TABLE IF NOT EXISTS options_metrics (
    symbol         TEXT NOT NULL,
    expiry         TEXT NOT NULL,
    as_of          INTEGER NOT NULL,
    spot           REAL,
    max_pain       REAL,
    pcr            REAL,
    gex_total      REAL,
    gex_sign       TEXT,
    gex_flip_level REAL,
    computed_at    INTEGER,
    PRIMARY KEY (symbol, expiry, as_of)
);
