-- Layer 3 schema additions for the parser/extractor pipeline.
-- Adds columns covering: classification, lifecycle tracking, storage paths,
-- text extraction, financial extraction, sentiment, and vision escalation.
-- See FINAL_ARCHITECTURE.md §7.1 for existing columns this builds on.

-- Classification & routing
ALTER TABLE raw_announcements ADD COLUMN pdf_type TEXT;
ALTER TABLE raw_announcements ADD COLUMN pdf_classified_at INTEGER;
ALTER TABLE raw_announcements ADD COLUMN pdf_size_bytes INTEGER;
ALTER TABLE raw_announcements ADD COLUMN pdf_page_count INTEGER;

-- Lifecycle tracking
ALTER TABLE raw_announcements ADD COLUMN pdf_status_updated_at INTEGER;
ALTER TABLE raw_announcements ADD COLUMN pdf_download_attempts INTEGER DEFAULT 0;
ALTER TABLE raw_announcements ADD COLUMN pdf_error TEXT;
ALTER TABLE raw_announcements ADD COLUMN pdf_sha256 TEXT;

-- Text extraction (Phase 2)
ALTER TABLE raw_announcements ADD COLUMN pdf_text_extracted_at INTEGER;
ALTER TABLE raw_announcements ADD COLUMN pdf_text_length INTEGER;

-- Financial extraction (Phase 3+)
ALTER TABLE raw_announcements ADD COLUMN extraction_confidence REAL;
ALTER TABLE raw_announcements ADD COLUMN extraction_strategy TEXT;
ALTER TABLE raw_announcements ADD COLUMN extraction_attempted_at INTEGER;
ALTER TABLE raw_announcements ADD COLUMN extraction_version TEXT;

-- Sentiment (Phase 6)
ALTER TABLE raw_announcements ADD COLUMN sentiment_scored_at INTEGER;
ALTER TABLE raw_announcements ADD COLUMN sentiment_version TEXT;

-- Vision escalation (Phase 5)
ALTER TABLE raw_announcements ADD COLUMN vision_attempted_at INTEGER;
ALTER TABLE raw_announcements ADD COLUMN vision_cost_usd REAL;
ALTER TABLE raw_announcements ADD COLUMN vision_model TEXT;

-- Partial indices for parser work queues. Partial so they stay small as
-- the table grows — only rows in active work states are indexed.
CREATE INDEX IF NOT EXISTS idx_ann_pdf_status
  ON raw_announcements(pdf_status, priority);

CREATE INDEX IF NOT EXISTS idx_ann_pending_work
  ON raw_announcements(pdf_status, broadcast_dt)
  WHERE pdf_status IN ('pending', 'classified', 'text_extracted');

CREATE INDEX IF NOT EXISTS idx_ann_extraction_review
  ON raw_announcements(pdf_status, broadcast_dt DESC)
  WHERE pdf_status IN ('extracted_low_conf', 'flagged_for_review');