-- Layer 2 / Phase 2: raw_announcements
-- Columns priority, pdf_path, pdf_text, extracted, sentiment, retention_policy
-- are populated by Layer 3 (parsers + retention). Phase 2 leaves them NULL.

CREATE TABLE IF NOT EXISTS raw_announcements (
    fingerprint       TEXT PRIMARY KEY,
    segment           TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    company_name      TEXT,
    subject           TEXT NOT NULL,
    details           TEXT,
    attachment_url    TEXT,
    broadcast_dt      TEXT NOT NULL,
    receipt_dt        TEXT,
    dissemination_dt  TEXT,
    priority          TEXT,
    pdf_path          TEXT,
    pdf_text          TEXT,
    extracted         TEXT,
    sentiment         TEXT,
    pdf_status        TEXT DEFAULT 'pending',
    retention_policy  TEXT,
    deleted_at        INTEGER,
    created_at        INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ann_symbol_ts
    ON raw_announcements(symbol, broadcast_dt DESC);

CREATE INDEX IF NOT EXISTS idx_ann_priority
    ON raw_announcements(priority, broadcast_dt DESC);