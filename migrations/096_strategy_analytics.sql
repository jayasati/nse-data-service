-- Strategy analytics — generic backtest results for the UI (strategy-agnostic, so any future
-- strategy persists here and shows up in the dashboard with no new schema).
CREATE TABLE IF NOT EXISTS strategy_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy       TEXT NOT NULL,
    run_date       TEXT,
    params_json    TEXT,
    n_symbols      INTEGER,
    n_trades       INTEGER,
    win_rate       REAL,
    profit_factor  REAL,
    expectancy     REAL,
    net_pct        REAL,
    max_dd_pct     REAL,
    sharpe         REAL,
    avg_rr         REAL,
    start_date     TEXT,
    end_date       TEXT,
    notes          TEXT,
    created_at     INTEGER
);

CREATE TABLE IF NOT EXISTS strategy_trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL,
    strategy     TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    direction    TEXT,            -- long | short
    daily_trend  TEXT,
    sweep_ts     TEXT,
    bos_ts       TEXT,
    fvg_low      REAL,
    fvg_high     REAL,
    entry_ts     TEXT,
    exit_ts      TEXT,
    entry_price  REAL,
    exit_price   REAL,
    stop         REAL,
    target       REAL,
    qty          INTEGER,
    rr           REAL,            -- planned reward:risk
    rr_achieved  REAL,
    net_pnl      REAL,
    net_pct      REAL,
    exit_reason  TEXT,            -- target | stop | time
    regime       TEXT
);
CREATE INDEX IF NOT EXISTS idx_strategy_trades_run ON strategy_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_strategy_trades_sym ON strategy_trades(run_id, symbol);
