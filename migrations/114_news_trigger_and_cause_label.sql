-- Task 5 (news long-tail coverage + auditable attribution).
-- raw_news.trigger_source: how the article entered the corpus — 'scheduled' (the 16:30
-- universe sweep) vs 'move_triggered' (a >5% intraday move pulled it in retroactively).
ALTER TABLE raw_news ADD COLUMN trigger_source TEXT DEFAULT 'scheduled';

-- move_causes.cause_label: makes the "we looked and found nothing" case auditable and
-- distinct from "we never looked". Populated by find_cause:
--   <category>       — a dated catalyst explained the move (company/sector/macro/...)
--   'no_news_found'  — attribution ran, news search returned ZERO articles (genuine catalyst-less)
--   'unknown'        — news existed but none plausibly explained the move
-- (Absence of a move_causes row entirely = attribution was never attempted.)
ALTER TABLE move_causes ADD COLUMN cause_label TEXT;
