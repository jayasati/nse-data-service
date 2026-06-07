-- Full EOD indicator set (FEATURE_CHECKLIST Phase 4, Week 12, tasks 12.1/12.2).
--
-- One settled daily row per symbol with the richer indicator set. Computed
-- nightly by the EOD job (indicators/eod_full.py); the live snapshot reads the
-- latest row each minute and compares it against the live price (same hybrid
-- pattern as indicator_sma → trend_regime).

CREATE TABLE IF NOT EXISTS indicator_eod (
    symbol         TEXT NOT NULL,
    date           TEXT NOT NULL,
    ema9           REAL,
    ema21          REAL,
    bb_upper       REAL,
    bb_lower       REAL,
    bb_width       REAL,        -- (upper − lower) / SMA20
    bb_squeeze     INTEGER,     -- 1 when width < 20th pct of last 252d, else 0
    adx            REAL,
    di_plus        REAL,
    di_minus       REAL,
    supertrend     REAL,        -- supertrend line (period 10, mult 2.0)
    supertrend_dir INTEGER,     -- 1 = up/long, -1 = down/short
    obv            REAL,
    vol_sma20      REAL,
    volume_ratio   REAL,        -- volume / 20d avg volume
    PRIMARY KEY (symbol, date)
);

-- Live columns surfaced into indicator_live (read by the signal/confidence path).
ALTER TABLE indicator_live ADD COLUMN ema9 REAL;
ALTER TABLE indicator_live ADD COLUMN ema21 REAL;
ALTER TABLE indicator_live ADD COLUMN bb_upper REAL;
ALTER TABLE indicator_live ADD COLUMN bb_lower REAL;
ALTER TABLE indicator_live ADD COLUMN bb_squeeze INTEGER;
ALTER TABLE indicator_live ADD COLUMN adx REAL;
ALTER TABLE indicator_live ADD COLUMN supertrend_direction INTEGER;
ALTER TABLE indicator_live ADD COLUMN obv REAL;
