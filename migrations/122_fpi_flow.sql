-- FPI flow-regime signal — connects the orphaned raw_nsdl_fpi_daily (custody-side daily FPI net
-- flow) to a signal. MARKET-LEVEL (equity net buy/sell), NOT per-sector — NSDL's daily report has
-- no sector breakdown; true sector rotation needs the separate monthly sector-AUC report.
-- Risk-on/off context for the swing book + the desk note. Not auto-scored into conviction.
CREATE TABLE IF NOT EXISTS fpi_flow (
    as_of_date   TEXT PRIMARY KEY,
    net_1d_cr    REAL,
    net_5d_cr    REAL,           -- 5-session cumulative equity FPI net
    regime       TEXT,           -- FPI_RISK_ON | FPI_BUYING | FPI_NEUTRAL | FPI_SELLING | FPI_RISK_OFF
    created_at   TEXT DEFAULT (datetime('now'))
);
