-- Daily Sweep strategy — live signal log + alert dedup (one row per fired setup).
CREATE TABLE IF NOT EXISTS daily_sweep_signals (
    fingerprint  TEXT PRIMARY KEY,    -- symbol:entry_time:direction
    symbol       TEXT NOT NULL,
    direction    TEXT,
    daily_trend  TEXT,
    sweep_time   TEXT,
    bos_time     TEXT,
    fvg_low      REAL,
    fvg_high     REAL,
    entry_time   TEXT,
    entry_price  REAL,
    stop         REAL,
    target       REAL,
    qty          INTEGER,
    rr           REAL,
    alerted_at   INTEGER
);
