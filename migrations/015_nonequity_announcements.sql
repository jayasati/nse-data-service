-- Layer 2 / Phase 7: raw_nonequity_announcements
-- Corporate announcements for non-equity segments — debt (NCDs / CP / bonds)
-- and mf (mutual funds + ETFs) — from /api/corporate-announcements?index=<seg>.
-- Extensible to municipalBond / invitsreits via the same shape.
--
-- Separate from raw_announcements (equities + SME) because:
--   1. These rows usually have no equity `symbol` (raw_announcements.symbol is
--      NOT NULL). Here symbol is nullable — debt is always null; mf ETF rows
--      (e.g. ITBEES) may carry one.
--   2. Low signal for an equity bot, so metadata-only: attachment_url is kept
--      but rows are NOT routed through the Layer 3 PDF pipeline (which scans
--      raw_announcements for pending rows). Hence no pdf_status/pdf_text cols.
--
-- Fingerprint (PK) is a content tuple over segment|seq_id|symbol|company|
-- subject|broadcast_dt|attachment — NSE's seq_id is NOT reliably unique here
-- (the mf feed reuses one seq_id across an ETF-tagged + untagged variant of the
-- same disclosure), so the fingerprint can't lean on seq_id alone.

CREATE TABLE IF NOT EXISTS raw_nonequity_announcements (
    fingerprint       TEXT PRIMARY KEY,
    segment           TEXT NOT NULL,        -- 'debt' | 'mf'
    symbol            TEXT,                 -- usually null; mf ETFs may carry one
    seq_id            TEXT,
    company_name      TEXT,
    isin              TEXT,
    subject           TEXT NOT NULL,
    details           TEXT,
    attachment_url    TEXT,
    broadcast_dt      TEXT NOT NULL,
    receipt_dt        TEXT,
    dissemination_dt  TEXT,
    orgid             TEXT,
    created_at        INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nonequity_ann_segment_dt
    ON raw_nonequity_announcements(segment, broadcast_dt DESC);

CREATE INDEX IF NOT EXISTS idx_nonequity_ann_company
    ON raw_nonequity_announcements(company_name, broadcast_dt DESC);
