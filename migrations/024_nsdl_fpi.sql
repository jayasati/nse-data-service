-- Layer 2 / Phase 7: raw_nsdl_fpi_daily  (NSDL Daily Trends in FPI Investments)
-- NSDL is SEBI's designated custodian-side monitor of FPI activity. Its only
-- daily/EOD feed is "Daily Trends in FPI Investments" — confirmed (T-1) +
-- provisional (T) FPI flow as reported to the depository by Custodians, broken
-- down BY ASSET CLASS × INVESTMENT ROUTE. NOTE: NSDL does NOT publish a daily
-- per-custodian flow; per-custodian figures are the MONTHLY AUC report (a
-- holdings snapshot, not a flow). This is the daily custody-side flow and adds
-- the Debt/Debt-VRR/Debt-FAR/Hybrid/MF split the exchange-side `fii_dii` lacks.
--
-- Source: https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx (HTML report, no
-- JSON API). External — scraped via httpx with a browser UA, outside the NSE
-- SessionManager, like `screener`/`macro`. The page's anti-scrape JS (disabled
-- right-click / F12 / Ctrl+U) is client-only and does not affect httpx.
--
-- One row per (date, asset_class, investment_route). Routes are typically
-- 'Stock Exchange', 'Primary market & others', and 'Sub-total' (the per-asset
-- net); the grand total is stored as asset_class='Total', route='Total'. The
-- Sub-total / Total rows are AGGREGATES — filter on route to avoid double
-- counting (e.g. route='Sub-total' for per-asset-class net, or the two
-- component routes for the split). Net values are negative on net selling.
-- Only Net Investment is published in USD; gross figures are ₹ Crore only.
--
-- Keyed (as_of_date, asset_class, investment_route) so the daily EOD run
-- upserts idempotently: a later confirmed revision overwrites the provisional.

CREATE TABLE IF NOT EXISTS raw_nsdl_fpi_daily (
    as_of_date         TEXT NOT NULL,    -- report trade date, YYYY-MM-DD
    asset_class        TEXT NOT NULL,    -- Equity | Debt-General Limit | Debt-VRR | Debt-FAR | Hybrid | Mutual Funds | AIFs | Total
    investment_route   TEXT NOT NULL,    -- Stock Exchange | Primary market & others | Sub-total | <MF scheme> | Total
    gross_purchase_cr  REAL,             -- ₹ Crore
    gross_sales_cr     REAL,             -- ₹ Crore
    net_cr             REAL,             -- ₹ Crore (negative = net sell)
    net_usd_mn         REAL,             -- Net Investment, US$ Million
    conversion_rate    REAL,             -- USD→INR used by NSDL for the day
    report_date_label  TEXT,             -- date as printed on the page, verbatim ("26-May-2026")
    captured_at        INTEGER NOT NULL,
    PRIMARY KEY (as_of_date, asset_class, investment_route)
);

CREATE INDEX IF NOT EXISTS idx_nsdl_fpi_date ON raw_nsdl_fpi_daily(as_of_date DESC);
