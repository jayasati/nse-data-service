-- Layer 2 / Phase 7: raw_unsolicited_watchlist + blacklist view extension
-- SEBI/NSE "Unsolicited Messages" watchlist — securities being promoted via
-- unsolicited SMS / pump-dump tips. Source is an XLSX:
--   https://nsearchives.nseindia.com/web/sites/default/files/inline-files/Current_list_of_symbols_1.xlsx
-- (page: /static/regulations/unsolicited-messages-report). Sheet "Current",
-- columns: Sr. No. | Date of Dissemination | Symbol | Scrip Code | Name | Remarks
-- | Company Response. The list is often empty (no active watchlist), which the
-- diff collector handles by emptying the table.
--
-- This IS a hard blacklist source (pump-dump targets → exclude from signals),
-- so the table is unioned into the blacklist view below. ReferenceCollector
-- (diff_upsert, key=symbol): a symbol entering the watchlist -> inserted,
-- leaving -> removed. No capture timestamp, so the diff stays meaningful.

CREATE TABLE IF NOT EXISTS raw_unsolicited_watchlist (
    symbol             TEXT PRIMARY KEY,
    scrip_code         TEXT,
    company_name       TEXT,
    date_disseminated  TEXT,
    remarks            TEXT,
    company_response   TEXT
);

-- Extend the blacklist view to include the unsolicited watchlist. A view can't
-- be altered in place, so drop + recreate with the original three feeds plus
-- the new one. Same column shape as 004 (symbol, feed, stage, reason, as_on,
-- fetched_at); unsolicited has no stage/fetched_at so those are NULL.
DROP VIEW IF EXISTS blacklist;
CREATE VIEW blacklist AS
    SELECT symbol, 'GSM' AS feed, stage, surv_code AS reason, as_on, fetched_at
      FROM raw_surveillance_gsm
    UNION ALL
    SELECT symbol, 'ASM-LT', stage, surv_code, as_on, fetched_at
      FROM raw_surveillance_asm_lt
    UNION ALL
    SELECT symbol, 'ASM-ST', stage, surv_code, as_on, fetched_at
      FROM raw_surveillance_asm_st
    UNION ALL
    SELECT symbol, 'UNSOLICITED', NULL, remarks, date_disseminated, NULL
      FROM raw_unsolicited_watchlist;
