-- Participant-wise F&O open interest + smart-money composite (FEATURE_CHECKLIST Week 22).
-- raw_participant_oi: NSE daily participant report (Client/DII/FII/Pro × instrument legs).
CREATE TABLE IF NOT EXISTS raw_participant_oi (
    report_date         TEXT NOT NULL,
    client_type         TEXT NOT NULL,     -- Client | DII | FII | Pro
    fut_idx_long        INTEGER, fut_idx_short        INTEGER,
    fut_stk_long        INTEGER, fut_stk_short        INTEGER,
    opt_idx_call_long   INTEGER, opt_idx_put_long     INTEGER,
    opt_idx_call_short  INTEGER, opt_idx_put_short    INTEGER,
    opt_stk_call_long   INTEGER, opt_stk_put_long     INTEGER,
    opt_stk_call_short  INTEGER, opt_stk_put_short    INTEGER,
    total_long          INTEGER, total_short          INTEGER,
    fetched_at          INTEGER,
    PRIMARY KEY (report_date, client_type)
);

-- smart_money_daily: weighted 0-1 institutional-positioning score (task 22.5).
CREATE TABLE IF NOT EXISTS smart_money_daily (
    as_of_date    TEXT PRIMARY KEY,
    score         REAL,              -- composite 0-1 (>0.70 accumulation, <0.40 distribution)
    fii_cash      REAL,
    fii_deriv     REAL,
    dii           REAL,
    block_tier    REAL,
    components    TEXT,              -- csv of which components contributed
    updated_at    INTEGER
);
