-- Signal engine MVP (FEATURE_CHECKLIST Week 4, task 4.1).
--
-- Four tables, one pipeline:
--
--   signals          — every setup the detector fires, one row per (re-)fire.
--                      The dedup layer (sigdedup:* in Redis, 30-min TTL) keeps
--                      the same symbol+type from spamming this table minute over
--                      minute, so a row here is a genuinely fresh alert.
--   signal_features  — a snapshot of EVERY live indicator value at signal time,
--                      one row per signal. This is the ML training archive and
--                      must be populated from day one (task 4.9) — we can't
--                      reconstruct the live `indicator_live` state after the
--                      fact, so if it isn't captured at fire time it's lost.
--   paper_trades     — the simulated book (Week 5). Created now so the schema is
--                      stable from the first signal; the tracker fills it later.
--   signal_outcomes  — forward-return labels the nightly labeler writes (Week 5).
--
-- `detected_at`, `dispatched_at`, `*_time`, `labeled_at` are ISO-8601 IST
-- strings (same convention as indicator_live.updated_at). Epoch-second columns
-- are avoided here so the rows are human-readable in the alert path.

CREATE TABLE IF NOT EXISTS signals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol            TEXT NOT NULL,
    signal_type       TEXT NOT NULL,   -- 'long_buildup' | 'breakout_52wh'
    detected_at       TEXT NOT NULL,   -- ISO-8601 IST
    price             REAL,
    oi_change_pct     REAL,
    price_change_pct  REAL,
    volume_ratio      REAL,
    confidence        REAL,            -- NULL until the Week-5 scorer fills it
    dispatched        INTEGER NOT NULL DEFAULT 0,
    dispatched_at     TEXT
);

-- The dispatcher (Week 5) polls for undispatched rows; index that hot filter.
CREATE INDEX IF NOT EXISTS idx_signals_dispatched
    ON signals(dispatched, detected_at);

-- The outcome labeler and dashboards scan "signals for symbol X on day Y".
CREATE INDEX IF NOT EXISTS idx_signals_symbol_detected
    ON signals(symbol, detected_at DESC);


CREATE TABLE IF NOT EXISTS signal_features (
    signal_id       INTEGER PRIMARY KEY,
    symbol          TEXT,
    detected_at     TEXT,

    -- Live indicator values copied from indicator_live / Redis ind:{symbol}
    -- at fire time. Grows as new live indicators are added (task 4.9 note).
    vwap            REAL,
    vwap_slope      REAL,
    rsi_5m          REAL,
    trend_regime    TEXT,
    momentum_state  TEXT,
    atr_14_daily    REAL,
    volume_ratio    REAL,
    market_regime   TEXT,

    FOREIGN KEY (signal_id) REFERENCES signals(id)
);


CREATE TABLE IF NOT EXISTS paper_trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id     INTEGER,
    symbol        TEXT,
    signal_type   TEXT,
    entry_price   REAL,
    sl_price      REAL,
    t1_price      REAL,
    entry_time    TEXT,
    exit_price    REAL,
    exit_time     TEXT,
    exit_reason   TEXT,   -- 'hit_t1' | 'hit_sl' | 'forced_flat' | 'manual'
    gross_pnl     REAL,
    net_pnl       REAL,   -- after the full cost model
    status        TEXT,   -- 'open' | 'closed'
    FOREIGN KEY (signal_id) REFERENCES signals(id)
);

-- The minute-cadence tracker (Week 5) scans all open trades each pass.
CREATE INDEX IF NOT EXISTS idx_paper_trades_status
    ON paper_trades(status);


CREATE TABLE IF NOT EXISTS signal_outcomes (
    signal_id     INTEGER PRIMARY KEY,
    symbol        TEXT,
    detected_at   TEXT,
    ret_30m       REAL,
    ret_2h        REAL,
    ret_eod       REAL,
    ret_1d        REAL,
    ret_3d        REAL,
    mae           REAL,   -- max adverse excursion
    mfe           REAL,   -- max favorable excursion
    hit_t1        INTEGER,
    hit_sl        INTEGER,
    labeled_at    TEXT,
    FOREIGN KEY (signal_id) REFERENCES signals(id)
);
