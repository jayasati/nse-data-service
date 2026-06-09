-- Results calendar (Phase 5 — Event Intelligence, E2).
--
-- Upcoming quarterly-result events per symbol, so the pre-screen job can flag a
-- stock BEFORE its result (buy-rumor / sell-rumor priced-in warnings) and the
-- post-result trigger knows a result is expected. Built nightly by
-- events/calendar.py from raw_board_meetings (results purpose) plus a T+45
-- cadence fallback from raw_financial_results history.

CREATE TABLE IF NOT EXISTS pending_events (
    symbol         TEXT NOT NULL,
    event_type     TEXT NOT NULL,        -- 'result'
    expected_date  TEXT NOT NULL,        -- 'YYYY-MM-DD'
    source         TEXT,                 -- 'board_meeting' | 'cadence'
    confidence     REAL,                 -- 0..1 (board_meeting high, cadence lower)
    status         TEXT NOT NULL DEFAULT 'upcoming',  -- 'upcoming'|'filed'|'expired'
    purpose        TEXT,                 -- raw board-meeting purpose text, if any
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER,
    PRIMARY KEY (symbol, expected_date, event_type)
);

CREATE INDEX IF NOT EXISTS idx_pending_events_date
    ON pending_events(status, expected_date);
