-- Per-quarter detailed shareholding (institutional split) — the Ownership-engine
-- feed. The master (raw_shareholding_pattern) holds only the LATEST quarter's
-- headline + the XBRL url; this parses that XBRL into FII/DII/MF/promoter and is
-- keyed by (symbol, qe_date) so a quarterly snapshot ACCUMULATES HISTORY (NSE
-- exposes SHP XBRL only for the current quarter, so deltas/backtest build forward).
-- Parser: parsers/shp_xbrl.py; filler: scripts/fill_shareholding_xbrl.py.

CREATE TABLE IF NOT EXISTS raw_shareholding_quarterly (
    symbol        TEXT NOT NULL,
    qe_date       TEXT NOT NULL,          -- quarter-end (from the master row)
    promoter_pct  REAL,
    public_pct    REAL,
    fii_pct       REAL,                   -- InstitutionsForeign (FPI)
    dii_pct       REAL,                   -- InstitutionsDomestic
    mf_pct        REAL,                   -- MutualFundsOrUTI (subset of DII)
    xbrl_url      TEXT,
    fetched_at    INTEGER,
    PRIMARY KEY (symbol, qe_date)
);
