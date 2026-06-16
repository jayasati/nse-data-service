-- BSE scrip master: ISIN → BSE numeric scrip code. Lights up the (already
-- written) BSE-announcements connector in cause_engine, which needs the numeric
-- strScrip the BSE API requires. ISIN-keyed so it is universe-independent and
-- reusable: cause_engine._bse_scrip joins raw_quote_metadata.isin → bse_code.
-- Populated by scripts/load_bse_scrip_master.py from BSE's active-equity list
-- JSON API (api.bseindia.com ListofScripData; SCRIP_CD / ISIN_NUMBER / Scrip_Name).

CREATE TABLE IF NOT EXISTS raw_bse_scrip_master (
    isin          TEXT PRIMARY KEY,
    bse_code      TEXT NOT NULL,     -- numeric strScrip, stored as text
    security_name TEXT,
    fetched_at    INTEGER
);
