-- Task 3 — make universe gaps auditable + a home for isolated small-cap signals.

-- Any stock that moves > threshold and is in NEITHER tradeable_universe NOR the small-cap track
-- gets logged here daily, turning silent coverage gaps into visible, reviewable decisions.
CREATE TABLE IF NOT EXISTS universe_gaps (
    ts          INTEGER,
    gap_date    TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    move_pct    REAL,
    reason_out  TEXT,                  -- 'illiquid' | 'no_data' | 'circuit_risk' | 'unknown'
    PRIMARY KEY (gap_date, symbol)
);

-- Small-cap EOD momentum signals — ISOLATED from the F&O conviction engine. Paper trades go to
-- paper_book tagged strategy='smallcap_momentum'; this table is the raw signal audit trail.
CREATE TABLE IF NOT EXISTS smallcap_signals (
    signal_date     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    close           REAL,
    move_pct        REAL,              -- close vs prior close
    vol_ratio       REAL,              -- day volume / 20-session avg
    is_52w_breakout INTEGER,
    delivery_pct    REAL,
    signal          TEXT,              -- the trigger(s) that fired
    PRIMARY KEY (signal_date, symbol)
);
