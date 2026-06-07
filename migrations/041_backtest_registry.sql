-- Experiment registry (FEATURE_CHECKLIST Phase 3, Week 11, task 11.1).
--
-- One row per evaluated strategy run: the headline metrics, the CPCV-derived
-- cost drag, and a verdict (promoted/shelved/needs_work). This is the durable
-- record of every promote/shelve decision and the bar (the ORB benchmark) that
-- future strategies must beat. `param_hash` makes a (strategy, params, range)
-- run identifiable so re-runs are comparable.

CREATE TABLE IF NOT EXISTS backtest_registry (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date         TEXT,                -- ISO-8601 IST when recorded
    strategy_name    TEXT,
    param_hash       TEXT,                -- sha256 of the strategy params JSON
    date_range       TEXT,                -- 'YYYY-MM-DD..YYYY-MM-DD' or 'full'
    net_sharpe       REAL,
    gross_sharpe     REAL,
    win_rate         REAL,
    profit_factor    REAL,
    max_drawdown_pct REAL,
    n_trades         INTEGER,
    cost_drag_pct    REAL,                -- gross_sharpe - net_sharpe
    cpcv_avg_sharpe  REAL,                -- mean net Sharpe across temporal folds
    cpcv_folds_pos   INTEGER,             -- how many folds had positive net Sharpe
    verdict          TEXT,                -- 'promoted' | 'shelved' | 'needs_work'
    notes            TEXT
);

CREATE INDEX IF NOT EXISTS idx_backtest_registry_strategy
    ON backtest_registry(strategy_name, run_date DESC);
