-- Layer 2 / Phase 7: raw_price_bands  (daily price-band master)
-- Source: https://nsearchives.nseindia.com/content/equities/sec_list.csv
-- A daily CSV (Symbol, Series, Security Name, Band, Remarks) listing every
-- equity security's applicable price band for the session. Pulled before
-- market open so the band restrictions are known ahead of trading.
--
-- This is the "Price band stages (2/5/10/20%)" feed. It ALSO carries the
-- T2T / restricted-segment classification via Series (EQ = rolling; BE/BZ/ST
-- = trade-for-trade / restricted), so "T2T segment" is a Series filter over
-- this table rather than a separate feed. Remarks surfaces surveillance notes
-- (e.g. 'GSM STAGE - II') that correlate with the tight band-2 securities.
--
-- ReferenceCollector (diff_upsert, key = symbol+series): the current file IS
-- the truth. A security tightening from band 20 -> 2 shows as 'updated'; a
-- security leaving the list as 'removed'. Key is (symbol, series) because a
-- symbol can list under two series (e.g. ELECTCAST as EQ + W1). NO capture
-- timestamp column, so the diff reflects genuine band changes, not churn.

CREATE TABLE IF NOT EXISTS raw_price_bands (
    symbol         TEXT NOT NULL,
    series         TEXT NOT NULL,   -- EQ | BE | BZ | ST | SM | IV | ...
    security_name  TEXT,
    band           INTEGER,         -- price band %; NULL for 'No Band'
    remarks        TEXT,            -- surveillance note, or NULL when '-'
    PRIMARY KEY (symbol, series)
);

CREATE INDEX IF NOT EXISTS idx_price_bands_band   ON raw_price_bands(band);
CREATE INDEX IF NOT EXISTS idx_price_bands_series ON raw_price_bands(series);
