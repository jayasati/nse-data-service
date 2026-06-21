-- Nightly per-stock news-impact scores (Phase-2 "news impact score"). news_score = positive flow
-- (50=quiet), news_risk = higher-is-safer; top_pos/top_neg name the driving events. Persisted by
-- register_news_score_job so the desk report reads scored news, not just stored headlines.
CREATE TABLE IF NOT EXISTS news_daily (
    as_of_date  TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    news_score  REAL,
    news_risk   REAL,
    n_events    INTEGER,
    top_pos     TEXT,
    top_neg     TEXT,
    updated_at  INTEGER,
    PRIMARY KEY (as_of_date, symbol)
);
CREATE INDEX IF NOT EXISTS ix_news_daily_date ON news_daily(as_of_date);
