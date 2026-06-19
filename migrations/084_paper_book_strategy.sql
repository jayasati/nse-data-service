-- Tag each paper_book position with the strategy that opened it, so several
-- strategies (Q+V+Mom, the regime-adaptive Buy Score, ...) can accumulate
-- independent forward track records in one book. Existing rows are the Q+V+Mom
-- strategy.
ALTER TABLE paper_book ADD COLUMN strategy TEXT NOT NULL DEFAULT 'qvm';
CREATE INDEX IF NOT EXISTS ix_paper_book_strategy ON paper_book (strategy, status);
