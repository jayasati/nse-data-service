-- True FPI SECTOR rotation — from NSDL's fortnightly "Sector-wise FPI Investment Data" static
-- reports (separate from the market-level daily feed). Per-sector net equity flow for the latest
-- fortnight + current AUC. Powers a sector-rotation read (which sectors foreign money is moving
-- into / out of) — surfaced in the brief + desk note, not auto-scored.
CREATE TABLE IF NOT EXISTS raw_fpi_sector (
    as_of_date      TEXT NOT NULL,      -- fortnight end date (ISO)
    period_label    TEXT,               -- e.g. 'JUNE 15, 2026'
    sector          TEXT NOT NULL,
    net_equity_cr   REAL,               -- net FPI equity investment in the fortnight (₹ cr)
    net_total_cr    REAL,               -- net across all asset classes (₹ cr)
    auc_equity_cr   REAL,               -- current equity assets-under-custody (₹ cr) — for scale
    captured_at     TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (as_of_date, sector)
);
CREATE INDEX IF NOT EXISTS ix_fpi_sector_date ON raw_fpi_sector(as_of_date);
