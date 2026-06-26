-- P3 — derived promoter/insider signal layer over raw_insider_trading (which carries category,
-- transaction_type, holding before/after but no signal). SWING-ONLY (10-30d horizon, constraint #8).
-- conviction_add is a SUGGESTED input — NOT auto-applied to the validation-gated conviction score.
CREATE TABLE IF NOT EXISTS promoter_signals (
    symbol              TEXT NOT NULL,
    filing_date         TEXT NOT NULL,
    acquirer_name       TEXT NOT NULL,
    acquirer_type       TEXT,                 -- PROMOTER | PROMOTER_GROUP | KMP | DIRECTOR | OTHER
    txn_type            TEXT,                 -- BUY | SELL | PLEDGE | PLEDGE_RELEASE | OTHER
    holding_change_pct  REAL,
    cumulative_buy_30d  REAL,
    signal_type         TEXT,                 -- PROMOTER_BUY_STRONG | PROMOTER_BUY | PROMOTER_SUSTAINED
                                              -- | PROMOTER_SELL_ALERT | PLEDGE_INCREASE | PLEDGE_DECREASE | NEUTRAL
    signal_strength     REAL,
    horizon_days        INTEGER,
    conviction_add      INTEGER,              -- suggested input only
    created_at          TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (symbol, filing_date, acquirer_name, txn_type)
);
CREATE INDEX IF NOT EXISTS ix_promoter_signals_date ON promoter_signals(filing_date);
