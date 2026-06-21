-- Persisted pre-market conviction snapshots (the 13-stage engine output) for the /conviction page.
CREATE TABLE IF NOT EXISTS conviction_daily (
    as_of_date TEXT NOT NULL, symbol TEXT NOT NULL,
    composite REAL, tier TEXT,
    catalyst REAL, positioning REAL, options REAL, structure REAL,
    volume REAL, rel_strength REAL, vol_expansion REAL,
    data_gaps TEXT, stages_json TEXT, updated_at INTEGER,
    PRIMARY KEY (as_of_date, symbol)
);
CREATE INDEX IF NOT EXISTS ix_conviction_date ON conviction_daily(as_of_date);
CREATE TABLE IF NOT EXISTS conviction_macro (
    as_of_date TEXT PRIMARY KEY, macro_json TEXT, smart_money REAL, updated_at INTEGER
);
