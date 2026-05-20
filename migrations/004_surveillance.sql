-- Layer 2 / Phase 5: surveillance feeds + unified blacklist
-- Three feeds, one collector. Each is a ReferenceCollector — daily snapshot
-- where "current state IS the truth." diff_upsert tracks add/remove/update
-- so /blacklist/changes?since= can answer "what changed today?"

CREATE TABLE IF NOT EXISTS raw_surveillance_gsm (
    symbol         TEXT PRIMARY KEY,
    company_name   TEXT,
    isin           TEXT,
    stage          TEXT,           -- "LXII", "I", "VI" — the Roman-numeral stage
    surv_code      TEXT,           -- "IBC - Receipt & GSM 0 (62)" — short code
    surv_desc      TEXT,           -- long description
    as_on          TEXT,           -- NSE's own gsmTime
    fetched_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_surveillance_asm_lt (
    symbol         TEXT PRIMARY KEY,
    series         TEXT,
    company_name   TEXT,
    isin           TEXT,
    stage          TEXT,           -- "Stage I", "Stage II", "Stage III", "Stage IV"
    surv_code      TEXT,           -- "LTASM - I (13)"
    surv_desc      TEXT,
    as_on          TEXT,
    fetched_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_surveillance_asm_st (
    symbol         TEXT PRIMARY KEY,
    series         TEXT,
    company_name   TEXT,
    isin           TEXT,
    stage          TEXT,           -- same vocabulary as asm_lt
    surv_code      TEXT,           -- "STASM - I (11)"
    surv_desc      TEXT,
    as_on          TEXT,
    fetched_at     INTEGER NOT NULL
);

-- The unified blacklist. A view, not a table — recomputed on every read,
-- so the moment a row leaves raw_*, it leaves the blacklist too. This is
-- what Layer 6's hard-filter check queries.
CREATE VIEW IF NOT EXISTS blacklist AS
    SELECT
        symbol,
        'GSM' AS feed,
        stage,
        surv_code AS reason,
        as_on,
        fetched_at
      FROM raw_surveillance_gsm
    UNION ALL
    SELECT symbol, 'ASM-LT', stage, surv_code, as_on, fetched_at
      FROM raw_surveillance_asm_lt
    UNION ALL
    SELECT symbol, 'ASM-ST', stage, surv_code, as_on, fetched_at
      FROM raw_surveillance_asm_st;

CREATE INDEX IF NOT EXISTS idx_surv_gsm_fetched   ON raw_surveillance_gsm(fetched_at);
CREATE INDEX IF NOT EXISTS idx_surv_asm_lt_fetched ON raw_surveillance_asm_lt(fetched_at);
CREATE INDEX IF NOT EXISTS idx_surv_asm_st_fetched ON raw_surveillance_asm_st(fetched_at);