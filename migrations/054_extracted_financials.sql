-- Extracted quarterly financials (Phase 5 — Event Intelligence, E1).
--
-- Persists the headline P&L numbers produced by parsers/financial_extractor.py
-- (vision-first) so the system has a per-quarter history per symbol and can
-- compute YoY/QoQ growth (the fundamental-surprise input to the earnings engine).
--
-- One row per (symbol, period_ending, scope). `scope` separates the standalone
-- and consolidated statements (both can be present in one filing).
-- `source_fingerprint` is the raw_announcements fingerprint the numbers came
-- from, so a re-run / backfill is idempotent (INSERT OR REPLACE on the PK).

CREATE TABLE IF NOT EXISTS extracted_financials (
    symbol                          TEXT NOT NULL,
    period_ending                   TEXT NOT NULL,   -- 'YYYY-MM-DD' quarter end
    scope                           TEXT NOT NULL,   -- 'standalone' | 'consolidated'
    relating_to                     TEXT,            -- 'First Quarter', etc. (if known)
    financial_year                  TEXT,

    -- the 10 canonical fields (crore, except EPS which is per-share rupees)
    revenue_cr                      REAL,
    other_income_cr                 REAL,
    total_income_cr                 REAL,
    total_expenses_cr               REAL,
    pbt_cr                          REAL,
    tax_cr                          REAL,
    pat_cr                          REAL,
    total_comprehensive_income_cr   REAL,
    eps_basic                       REAL,
    eps_diluted                     REAL,

    units_phrase                    TEXT,            -- source unit the model reported
    extract_confidence              REAL,
    strategy                        TEXT,            -- 'vision' | 'text_llm' | ...
    source_fingerprint              TEXT,            -- raw_announcements.fingerprint
    broadcast_dt                    TEXT,
    extracted_at                    INTEGER NOT NULL,

    PRIMARY KEY (symbol, period_ending, scope)
);

CREATE INDEX IF NOT EXISTS idx_extfin_symbol
    ON extracted_financials(symbol, period_ending DESC);

CREATE INDEX IF NOT EXISTS idx_extfin_source
    ON extracted_financials(source_fingerprint);
