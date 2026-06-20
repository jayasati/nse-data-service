-- F&O securities-in-ban list (PROFITABILITY_PLAN R13). One row per (symbol, ban_date) —
-- accumulates a forward history of which names were in F&O ban on which day (a record we
-- have NONE of historically, so the gate can only be validated forward). For a delivery
-- book the ban is a RISK FLAG (the ban restricts derivatives, not cash equity), shown on
-- the pre-buy card next to ASM/GSM.
CREATE TABLE IF NOT EXISTS raw_fno_ban (
    symbol     TEXT NOT NULL,
    ban_date   TEXT NOT NULL,     -- ISO date the list was captured
    fetched_at INTEGER,
    PRIMARY KEY (symbol, ban_date)
);
