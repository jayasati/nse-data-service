-- Clean ANNUAL financials (full-year P&L + cash flow + year-end balance sheet) from audited
-- integrated filings, parsed via xbrl_financials.parse_annual. The consistent annual basis the
-- Piotroski F-score needs — quarterly extracted_financials lacks cash flow + prior-year. One row
-- per (symbol, scope, fiscal-year-end); stacking years gives the prior-year comparison.
CREATE TABLE IF NOT EXISTS annual_financials (
    symbol                 TEXT NOT NULL,
    scope                  TEXT NOT NULL,      -- consolidated | standalone
    fy_ending              TEXT NOT NULL,      -- fiscal year-end (ISO)
    pat_cr                 REAL,
    pbt_cr                 REAL,
    finance_cost_cr        REAL,
    cfo_cr                 REAL,               -- net cash from OPERATING activities (full year)
    revenue_cr             REAL,
    cost_of_materials_cr   REAL,
    total_assets_cr        REAL,
    current_assets_cr      REAL,
    current_liabilities_cr REAL,
    total_liabilities_cr   REAL,
    borrowings_cr          REAL,
    equity_cr              REAL,
    eps_basic              REAL,
    xbrl_url               TEXT,
    captured_at            TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (symbol, scope, fy_ending)
);
CREATE INDEX IF NOT EXISTS ix_annual_fin_symbol ON annual_financials(symbol, fy_ending DESC);
