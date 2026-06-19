-- Engine-1 macro indicators alongside USDINR/Brent:
--   gpr     — Caldara-Iacoviello Geopolitical Risk index (daily, auto-fetched, reliable)
--   cpi_yoy — CPI YoY inflation % (best-effort/manual: FRED + data.gov.in are unreliable
--             from the ap-south-1 host; populated when a fetch succeeds or by hand)
-- Interest rates are read from the existing manual `raw_macro_rates` (repo + 10y G-sec),
-- since no free DAILY rates API is reliably reachable from the server.
ALTER TABLE raw_macro_market ADD COLUMN gpr REAL;
ALTER TABLE raw_macro_market ADD COLUMN cpi_yoy REAL;
