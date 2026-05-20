-- Layer 2 / Phase 4: raw_bhavcopy_cm
-- NSE's end-of-day OHLCV file. The truth source for indicators (Layer 4).
-- One row per (date, symbol, series). Series matters because the same SYMBOL
-- can appear under multiple series (e.g. equity + bonds), each a different
-- instrument with its own OHLCV.

CREATE TABLE IF NOT EXISTS raw_bhavcopy_cm (
    date          TEXT    NOT NULL,
    symbol        TEXT    NOT NULL,
    series        TEXT    NOT NULL,
    prev_close    REAL,
    open          REAL,
    high          REAL,
    low           REAL,
    last_price    REAL,
    close         REAL,
    avg_price     REAL,
    volume        INTEGER,
    turnover_lacs REAL,
    trades        INTEGER,
    delivery_qty  INTEGER,
    delivery_pct  REAL,
    PRIMARY KEY (date, symbol, series)
);

CREATE INDEX IF NOT EXISTS idx_bhav_cm_symbol_date
    ON raw_bhavcopy_cm(symbol, date DESC);

CREATE INDEX IF NOT EXISTS idx_bhav_cm_date
    ON raw_bhavcopy_cm(date DESC);