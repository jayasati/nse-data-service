-- Append-only daily snapshot of per-symbol conviction factor SCORES + direction/confluence.
-- Purpose: FORWARD, out-of-sample attribution → calibrate the factor weights from LIVE data over the
-- coming weeks (incl. options/positioning, which have NO backtest history). Forward returns are
-- joined from bhavcopy at analysis time (scripts/conviction_attribution.py). Written once/day by the
-- 09:10 pre-market run (not the intraday refreshes).
CREATE TABLE IF NOT EXISTS conviction_factor_log (
    snapshot_date  TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    direction      TEXT,
    conf_label     TEXT,
    conf_agreement INTEGER,
    conviction_adj REAL,
    composite      REAL,
    options        REAL,
    structure      REAL,
    positioning    REAL,
    volume         REAL,
    rel_strength   REAL,
    vol_expansion  REAL,
    entry_close    REAL,
    created_at     INTEGER,
    PRIMARY KEY (snapshot_date, symbol)
);
