-- Persistent per-stock news memory (plans/NEWS_MEMORY_PLAN.md). Today news is
-- ephemeral (live Google-News search per move, discarded). This is the standing
-- corpus the cause engine reads from and that deepens over time.
--
-- Two layers populate it (collect_news.py):
--   headline layer  — Google News RSS per symbol (broad; headline+url+date, no body)
--   full-text layer — ET Markets / Moneycontrol market RSS → direct URL → article_text
--                     (entity-resolved company→symbol)
-- published_epoch is INT (epoch, IST) — never a sortable-string date (broadcast_epoch lesson).

CREATE TABLE IF NOT EXISTS raw_news (
    fingerprint     TEXT PRIMARY KEY,    -- sha1(symbol|url) or sha1(symbol|title|date)
    symbol          TEXT,                -- resolved NSE symbol (NULL = market/unresolved)
    headline        TEXT NOT NULL,
    url             TEXT,
    source          TEXT,                -- 'google_news' | 'et_markets' | 'moneycontrol' | …
    published_epoch INTEGER,             -- IST epoch seconds (NULL if undated)
    published_raw   TEXT,                -- original date string (audit)
    snippet         TEXT,                -- RSS description / lead
    article_text    TEXT,                -- full body when fetched (NULL for headline-only)
    text_status     TEXT NOT NULL DEFAULT 'headline_only',  -- headline_only|ok|paywalled|failed
    fetched_at      INTEGER
);

CREATE INDEX IF NOT EXISTS ix_news_symbol_epoch ON raw_news(symbol, published_epoch);
CREATE INDEX IF NOT EXISTS ix_news_text_status ON raw_news(text_status);
