-- Phase 3 schema additions: extraction metadata columns.
-- The extracted JSON itself goes into the existing raw_announcements.extracted
-- column (added in Phase 1's migration 011). This migration adds the metadata
-- fields needed to track which extraction version/strategy/confidence
-- produced a given result, plus per-call cost tracking for LLM usage.

-- Note: extraction_confidence, extraction_strategy, extraction_attempted_at,
-- and extraction_version were ALL added in migration 011_layer3_columns.sql.
-- This migration only adds the LLM cost tracking + period fields.

ALTER TABLE raw_announcements ADD COLUMN period_label TEXT;
ALTER TABLE raw_announcements ADD COLUMN period_ending TEXT;
ALTER TABLE raw_announcements ADD COLUMN units_in_source_pdf TEXT;
ALTER TABLE raw_announcements ADD COLUMN llm_cost_usd REAL;
ALTER TABLE raw_announcements ADD COLUMN llm_input_tokens INTEGER;
ALTER TABLE raw_announcements ADD COLUMN llm_output_tokens INTEGER;

-- Index for finding rows ready for extraction
CREATE INDEX IF NOT EXISTS idx_ann_ready_extract
  ON raw_announcements(pdf_status, priority, subject)
  WHERE pdf_status = 'text_extracted' AND priority = 'high';