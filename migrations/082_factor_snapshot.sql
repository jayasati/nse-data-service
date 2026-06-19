-- P7/P8 feature store: one row per (snapshot_date, symbol) capturing every factor
-- engine's score + the validated composite + sector rank + market regime, plus the
-- realised forward sector-excess (net-of-cost) at 30/60/90/120d filled later by
-- scripts/label_snapshots.py once each horizon matures. This is the permanent
-- training/factor-research matrix (the arch doc's "historical snapshot system") —
-- ML stops self-bootstrapping from history once this has accrued enough rows.
CREATE TABLE IF NOT EXISTS factor_snapshot (
    snapshot_date   TEXT NOT NULL,           -- 'YYYY-MM-DD' (IST trading date)
    symbol          TEXT NOT NULL,
    sector          TEXT,
    grade           TEXT,                     -- tradeable_universe grade as-of
    -- factor engine scores [0,100] (NULL = engine didn't score that name)
    quality         REAL,
    valuation       REAL,
    momentum        REAL,
    turnaround      REAL,
    liquidity       REAL,
    surprise        REAL,
    ownership       REAL,
    risk            REAL,
    confidence      REAL,
    -- the validated composite (Quality+Valuation) + its sector ranking
    composite       REAL,
    sector_rank     INTEGER,                  -- 1 = best in sector on this date
    sector_n        INTEGER,                  -- peers ranked in the sector
    regime          TEXT,                     -- market_state.overall_regime as-of
    computed_epoch  INTEGER NOT NULL,
    -- realised forward sector-excess (%, net-of-cost); NULL until matured + labelled
    fwd_excess_30   REAL,
    fwd_excess_60   REAL,
    fwd_excess_90   REAL,
    fwd_excess_120  REAL,
    PRIMARY KEY (snapshot_date, symbol)
);
CREATE INDEX IF NOT EXISTS ix_factor_snapshot_symbol ON factor_snapshot (symbol, snapshot_date);
CREATE INDEX IF NOT EXISTS ix_factor_snapshot_date ON factor_snapshot (snapshot_date);
