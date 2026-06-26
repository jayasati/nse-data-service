-- P2 — entity-classified bulk/block deals. raw_large_deals carries client_name but no entity tag
-- or signal; this is the derived layer (who actually transacted + is it institutional flow).
-- 1:1 with raw_large_deals via fingerprint.
CREATE TABLE IF NOT EXISTS large_deal_signals (
    fingerprint   TEXT PRIMARY KEY,      -- = raw_large_deals.fingerprint
    deal_date     TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    deal_type     TEXT,                  -- 'BULK' | 'BLOCK'
    client_name   TEXT,
    txn_type      TEXT,                  -- 'BUY' | 'SELL'
    qty           INTEGER,
    price         REAL,
    value_cr      REAL,
    entity_type   TEXT,                  -- FII | MF | INSURANCE | PROMOTER | CORPORATE | INDIVIDUAL | UNKNOWN
    signal_type   TEXT,                  -- INSTITUTIONAL_BUY_LARGE | INSTITUTIONAL_BUY
                                         -- | INSTITUTIONAL_SELL_LARGE | PROMOTER_OPEN_MARKET_BUY | NULL
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_large_deal_signals_date ON large_deal_signals(deal_date);
