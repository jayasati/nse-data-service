-- P7 plumbing (SECTOR_RESULT_PLAYBOOK.md §4): narrative signals per filing.
--
-- The deciding sector KPIs often live in the press-release text bundled into
-- the result PDF, not the P&L tables: guidance (IT), volume growth (FMCG/auto),
-- order inflow (capital goods), FDA status (pharma), dividend, management tone.
-- parsers/narrative/extract_narrative lifts them from raw_announcements.pdf_text
-- at extraction time; the JSON (NarrativeFields.as_dict()) is stored here so the
-- result-quality detector and alert card can fold them into the sector verdict
-- without re-reading the PDF. NULL when the filing text carried none of them.
ALTER TABLE extracted_financials ADD COLUMN narrative_json TEXT;
