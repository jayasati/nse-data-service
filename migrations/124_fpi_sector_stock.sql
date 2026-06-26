-- Per-stock FPI sector-flow tag — the NSDL sector rotation mapped down to member stocks via NSE
-- sectoral-index membership. A liquid name in a sector with strong FPI flow gets a TAILWIND/HEADWIND
-- tag for the fortnight. Surfaced in the desk note + signals_today; NOT auto-scored into conviction.
CREATE TABLE IF NOT EXISTS fpi_sector_stock (
    as_of_date     TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    sector         TEXT,
    net_equity_cr  REAL,          -- the sector's fortnight net FPI equity flow (₹ cr)
    signal         TEXT,          -- FPI_SECTOR_TAILWIND | FPI_SECTOR_HEADWIND
    created_at     TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (as_of_date, symbol)
);
CREATE INDEX IF NOT EXISTS ix_fpi_sector_stock_date ON fpi_sector_stock(as_of_date);
