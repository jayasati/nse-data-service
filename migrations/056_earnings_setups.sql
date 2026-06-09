-- Pre-event earnings setup snapshot (Phase 5 — Event Intelligence, E2).
--
-- Frozen, just before a result, by events/pre_screen.py: what the market has
-- already priced in (run-up, options-implied move, positioning) plus the stock's
-- recent fundamental trend. This is the "expectation proxy" the post-result
-- trigger compares the actual reaction against. `flagged_at` dedups the
-- pre-event Telegram flag (send once per event).

CREATE TABLE IF NOT EXISTS earnings_setups (
    symbol                  TEXT NOT NULL,
    event_date              TEXT NOT NULL,      -- the pending_events.expected_date

    -- pre-result price run-up (the "buy-rumor / sell-rumor" priced-in read)
    run_up_5d               REAL,
    run_up_10d              REAL,
    run_up_class            TEXT,               -- BUY_RUMOR_IN_PLAY / MILD_ANTICIPATION / NORMAL / MILD_FEAR / FEAR_PRICED

    -- options positioning
    iv_atm                  REAL,               -- ATM implied volatility (%)
    implied_move_pct        REAL,               -- expected move into the event (%)
    pcr                     REAL,               -- put/call OI ratio
    oi_buildup_class        TEXT,               -- LONG_BUILDUP / SHORT_BUILDUP / NEUTRAL

    -- context
    sector_rank             INTEGER,
    sector_trend            TEXT,
    growth_yoy_rev          REAL,               -- last-known YoY revenue growth (%)
    growth_yoy_pat          REAL,               -- last-known YoY PAT growth (%)
    fundamental_class       TEXT,               -- STRONG_GROWTH / GROWTH / FLAT / DECLINE

    -- optional consensus (E4); null until the estimates layer exists
    consensus_rev_est       REAL,
    consensus_eps_est       REAL,

    expectation_proxy_score REAL,               -- composite lean, -1 (priced for fall) .. +1 (priced for rise)
    flagged_at              INTEGER,            -- when the pre-event flag was sent (null = not yet)
    created_at              INTEGER NOT NULL,

    PRIMARY KEY (symbol, event_date)
);

CREATE INDEX IF NOT EXISTS idx_earnings_setups_event
    ON earnings_setups(event_date);
