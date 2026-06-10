-- Policy / benchmark rates for the BFSI earnings-risk overlay (Week 17.5, S6).
--
-- The SBI 8-May lesson: the treasury MTM hit and NIM compression were
-- foreseeable in DIRECTION from public macro — a Dec-2025 repo cut repricing
-- EBLR/T-bill books (NIM down), and the 10Y G-sec hardening toward ~7% in Q4
-- (AFS mark-to-market loss). This table stores the two rates the pre-print risk
-- flag (S7) keys off: the RBI repo rate and the 10-year G-sec yield.
--
-- One row per as_of_date. Either column may be NULL (they update on different
-- cadences — repo a few times a year, the yield daily). `source` records where
-- the value came from ('manual' | '<feed name>') so a hand-entered seed and an
-- automated feed are distinguishable.
--
-- NOTE (S6): there is no reliable free API for the India 10Y G-sec yield or the
-- repo policy rate. record_rates() (market/macro_rates.py) is the working manual
-- ingestion path; an automated feed is a pending external-source decision.

CREATE TABLE IF NOT EXISTS raw_macro_rates (
    as_of_date       TEXT NOT NULL,    -- YYYY-MM-DD
    repo_rate        REAL,             -- RBI repo policy rate, %
    gsec_10y_yield   REAL,             -- 10-year G-sec benchmark yield, %
    source           TEXT,             -- 'manual' | feed name
    captured_at      INTEGER NOT NULL,
    PRIMARY KEY (as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_macro_rates_date ON raw_macro_rates(as_of_date DESC);
