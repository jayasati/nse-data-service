-- Layer 5: backtest results persistence.
-- `backtest_runs` is one row per invocation of the backtester; `backtest_trades`
-- is one row per filled trade in that run. The CLI runner writes both inside
-- a single transaction when --commit is passed.
--
-- pnl_raw   = (exit - entry) * direction          (signed)
-- pnl_leveraged = pnl_raw * run.leverage          (also signed)
-- Both are stored so the UI can toggle without recomputing.
--
-- No retention policy yet: rows are small (1 per trade) and the user wants
-- to compare runs over time. Drop runs manually with `DELETE FROM backtest_runs
-- WHERE id = ?` — trades cascade via the FK.

CREATE TABLE IF NOT EXISTS backtest_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy        TEXT    NOT NULL,
    params_json     TEXT    NOT NULL,
    universe        TEXT    NOT NULL,
    symbols_count   INTEGER NOT NULL,
    start_date      TEXT    NOT NULL,
    end_date        TEXT    NOT NULL,
    leverage        REAL    NOT NULL,
    total_signals   INTEGER NOT NULL DEFAULT 0,
    total_trades    INTEGER NOT NULL DEFAULT 0,
    wins            INTEGER NOT NULL DEFAULT 0,
    losses          INTEGER NOT NULL DEFAULT 0,
    pnl_raw         REAL    NOT NULL DEFAULT 0,
    pnl_leveraged   REAL    NOT NULL DEFAULT 0,
    max_dd_raw      REAL    NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    run_id          INTEGER NOT NULL,
    symbol          TEXT    NOT NULL,
    direction       TEXT    NOT NULL,            -- 'LONG' | 'SHORT'
    setup_ts        INTEGER NOT NULL,            -- bar at which signal detected
    entry_ts        INTEGER NOT NULL,            -- bar at which order filled
    entry_price     REAL    NOT NULL,
    sl              REAL    NOT NULL,
    target          REAL    NOT NULL,
    exit_ts         INTEGER NOT NULL,
    exit_price      REAL    NOT NULL,
    exit_reason     TEXT    NOT NULL,            -- 'TARGET' | 'STOP' | 'EOD_1515'
    qty             INTEGER NOT NULL DEFAULT 1,
    pnl_raw         REAL    NOT NULL,
    pnl_leveraged   REAL    NOT NULL,
    rr_at_entry    REAL    NOT NULL,
    PRIMARY KEY (run_id, symbol, entry_ts),
    FOREIGN KEY (run_id) REFERENCES backtest_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_bt_trades_run         ON backtest_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_bt_trades_run_symbol  ON backtest_trades(run_id, symbol);
CREATE INDEX IF NOT EXISTS idx_bt_trades_run_pnl     ON backtest_trades(run_id, pnl_raw);
CREATE INDEX IF NOT EXISTS idx_bt_runs_created       ON backtest_runs(created_at DESC);
