-- Cash flow from operations (Phase 5 — Week 17, task 17.7 earnings quality).
--
-- Optional: present only when a result filing includes a Cash Flow Statement
-- (half-year / annual results; absent in most quarterly P&L-only filings). Feeds
-- an earnings-quality read later (CFO/PAT ratio).

ALTER TABLE extracted_financials ADD COLUMN cfo_cr REAL;
