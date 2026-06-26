-- Task 5 (news long-tail coverage + auditable attribution).
-- raw_news.trigger_source: how the article entered the corpus — 'scheduled' (the 16:30
-- universe sweep) vs 'move_triggered' (a >5% intraday move pulled it in retroactively).
ALTER TABLE raw_news ADD COLUMN trigger_source TEXT DEFAULT 'scheduled';

-- NOTE: move_causes.cause_label is NOT added here. `move_causes` is created lazily at runtime
-- by move_causes.ensure_table() (no migration creates it), so an ALTER here fails on a fresh DB
-- ("no such table: move_causes"). ensure_table() adds cause_label idempotently instead — see the
-- additive-migration loop there. (Adding it here broke the migration runner on clean DBs/tests.)
