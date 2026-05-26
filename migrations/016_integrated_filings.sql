-- Layer 2 / Phase 7: raw_integrated_filings
-- SEBI "Integrated Filing" disclosures from /api/integrated-filing-results,
-- two types served separately by the `type` query param:
--   'Integrated Filing- Financials'  (quarterly financials, iXBRL/XBRL)
--   'Integrated Filing- Governance'  (governance disclosures)
--
-- Each is a large newest-first archive (~20k rows), so the collector pulls the
-- latest page per type weekly and dedups — same pattern as financial_results.
--
-- Fingerprint (PK) = filing_type|seq_id: seq_id is unique within a type but the
-- two types share a numeric id space, so the type prefix prevents collision.
-- A filing and its later Revision are separate archive rows with their own
-- seq_id, so both are kept (type_sub distinguishes Original vs Revision).

CREATE TABLE IF NOT EXISTS raw_integrated_filings (
    fingerprint      TEXT PRIMARY KEY,
    seq_id           TEXT,
    filing_type      TEXT,          -- 'Integrated Filing- Financials' | '...- Governance'
    type_sub         TEXT,          -- 'Original' | 'Revision'
    symbol           TEXT,
    company_name     TEXT,
    qe_date          TEXT,          -- quarter-end, e.g. '31-MAR-2026'
    audited          TEXT,          -- 'Audited' | 'Unaudited' | NULL
    consolidated     TEXT,          -- 'Consolidated' | 'Standalone' | NULL
    ixbrl_url        TEXT,
    xbrl_url         TEXT,
    pdf_url          TEXT,
    broadcast_dt     TEXT,
    revised_dt       TEXT,
    revision_remark  TEXT,
    creation_dt      TEXT,
    created_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intfiling_symbol
    ON raw_integrated_filings(symbol, qe_date);

CREATE INDEX IF NOT EXISTS idx_intfiling_type
    ON raw_integrated_filings(filing_type, creation_dt DESC);
