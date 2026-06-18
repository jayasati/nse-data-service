-- Forward paper-trade ledger for the validated Q+V+Momentum dynamic strategy
-- (scripts/paper_trade.py). One row per position; status open→closed. Accumulates
-- a FORWARD, out-of-sample track record — the real gate before sizing real money.
CREATE TABLE IF NOT EXISTS paper_book (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    entry_date  TEXT NOT NULL,
    entry_px    REAL,
    entry_score REAL,
    peak_score  REAL,            -- highest composite seen while held (for trailing exit)
    last_score  REAL,            -- most recent composite
    status      TEXT NOT NULL DEFAULT 'open',   -- 'open' | 'closed'
    exit_date   TEXT,
    exit_px     REAL,
    exit_reason TEXT,            -- 't_out' | 'trail' | 'max_hold' | 'stop' | 'dropped'
    net_pct     REAL,            -- realised %, net of round-trip cost
    updated_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_book(status, symbol);
