-- Opening Range (ORB) on 5-minute bars — session-anchored, recomputed every minute.
CREATE TABLE IF NOT EXISTS indicator_orb_5m (
    symbol     TEXT    NOT NULL,
    ts         INTEGER NOT NULL,        -- UTC epoch seconds, 5-min bar start
    orb_high   REAL,
    orb_low    REAL,
    orb_break  REAL,                    -- +1 above OR high · -1 below OR low · 0 inside
    PRIMARY KEY (symbol, ts)
);
CREATE INDEX IF NOT EXISTS idx_indicator_orb_5m_symbol_ts ON indicator_orb_5m(symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_indicator_orb_5m_ts ON indicator_orb_5m(ts DESC);
