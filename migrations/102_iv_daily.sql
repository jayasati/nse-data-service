-- ATM implied-vol snapshot per F&O symbol (builds the IV-percentile history for Stage 9).
CREATE TABLE IF NOT EXISTS iv_daily (
    as_of_date TEXT NOT NULL, symbol TEXT NOT NULL,
    atm_iv REAL, spot REAL, updated_at INTEGER,
    PRIMARY KEY (as_of_date, symbol)
);
CREATE INDEX IF NOT EXISTS ix_iv_daily_sym ON iv_daily(symbol);
