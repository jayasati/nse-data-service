-- Layer 2 / Phase 7: raw_india_vix  (India VIX + derived expected-range envelopes)
-- India VIX is NSE's 30-day annualized Nifty volatility index (percent). It
-- arrives in the same /api/allIndices payload the `indices` collector reads,
-- next to the NIFTY 50 spot. On every poll we record the VIX and derive the
-- expected daily move and the 1σ / 2σ price envelopes around the Nifty spot.
--
-- Envelope math (TRADING_DAYS = 252):
--   expected_move_pct (1σ, daily) = VIX / sqrt(252)
--   move_pts                      = nifty_spot * expected_move_pct / 100
--   sigmaN_upper/lower            = nifty_spot ± N * move_pts
-- expected_move_pct is stored raw so a downstream consumer can rescale (e.g.
-- to a calendar-day sqrt(365) convention) without re-fetching.
--
-- Keyed on (as_of) = capture unix time, so a 5-min market-hours poll
-- accumulates an intraday VIX/envelope series.

CREATE TABLE IF NOT EXISTS raw_india_vix (
    as_of             INTEGER NOT NULL, -- capture time (unix seconds)
    vix               REAL NOT NULL,    -- INDIA VIX last (annualized vol %)
    vix_open          REAL,
    vix_high          REAL,
    vix_low           REAL,
    vix_prev_close    REAL,
    vix_pct_change    REAL,
    nifty_spot        REAL,             -- NIFTY 50 last (envelope anchor)
    expected_move_pct REAL,             -- 1σ daily move, % of spot
    sigma1_upper      REAL,             -- nifty_spot + 1σ move (points)
    sigma1_lower      REAL,
    sigma2_upper      REAL,             -- nifty_spot + 2σ move (points)
    sigma2_lower      REAL,
    nse_timestamp     TEXT,             -- allIndices payload timestamp, verbatim
    captured_at       INTEGER NOT NULL,
    PRIMARY KEY (as_of)
);

CREATE INDEX IF NOT EXISTS idx_india_vix_asof ON raw_india_vix(as_of DESC);
