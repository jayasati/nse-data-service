-- Layer 2 / Phase 7: raw_shareholding_pattern
-- Per-symbol shareholding master from /api/corporate-share-holdings-master
-- (index=equities + index=sme). The headline ownership split each quarter:
-- promoter+promoter-group %, public %, employee-trust %. Feeds §5 Fundamentals
-- (promoter holding / public split) and Layer 6 pledge/ownership checks.
--
-- NOTE the endpoint path: corporate-share-holdings-master (hyphenated
-- "share-holdings"); the older corporate-shareholdings-master 404s.
--
-- ReferenceCollector (diff_upsert, key=symbol): the current master IS the
-- truth. A symbol's promoter_pct moving quarter-over-quarter shows up as an
-- 'updated' row; a delisting drops out as 'removed'. Deliberately NO capture
-- timestamp column — it would make every row diff as 'updated' each run and
-- mask the real quarterly changes. Provenance lives in qe_date / record_id /
-- broadcast_dt instead.

CREATE TABLE IF NOT EXISTS raw_shareholding_pattern (
    symbol             TEXT PRIMARY KEY,
    segment            TEXT,          -- 'equities' | 'sme'
    company_name       TEXT,
    industry           TEXT,
    isin               TEXT,
    qe_date            TEXT,          -- quarter-end the filing covers
    promoter_pct       REAL,          -- pr_and_prgrp
    public_pct         REAL,          -- public_val
    employee_trust_pct REAL,          -- employeeTrusts
    record_id          TEXT,
    xbrl_url           TEXT,
    revised            TEXT,          -- 'Y' | 'N'
    submission_date    TEXT,
    broadcast_dt       TEXT
);

CREATE INDEX IF NOT EXISTS idx_shp_segment
    ON raw_shareholding_pattern(segment);

CREATE INDEX IF NOT EXISTS idx_shp_promoter
    ON raw_shareholding_pattern(promoter_pct DESC);
