-- Job heartbeat: one row per scheduled job recording its last run, outcome, and a
-- consecutive-zero counter. Powers /api/health/paper_loop and the "paper loop is dark"
-- alert. Generic (job_id keyed) so other jobs can adopt it later.
CREATE TABLE IF NOT EXISTS job_heartbeat (
    job_id              TEXT PRIMARY KEY,
    last_run_utc        TEXT,      -- ISO8601 UTC of the last _tick
    status              TEXT,      -- 'ok' | 'skipped_non_trading_day' | 'failed'
    detail              TEXT,      -- human-readable summary (per-strategy buys, or error)
    items_booked        INTEGER,   -- e.g. paper trades opened this run
    consecutive_zero    INTEGER DEFAULT 0,  -- runs in a row that booked 0 (ok runs only)
    updated_at          TEXT
);
