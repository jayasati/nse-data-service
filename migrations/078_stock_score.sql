-- Per-stock conviction score in [0,1] — the aggregation layer over everything we
-- know (momentum, drawdown, order wins, analyst calls, news sentiment, regulatory).
-- Transparent: components_json stores each component's signed contribution.
-- A daily snapshot per symbol (history kept) so the score can be VALIDATED forward
-- (does high score precede higher returns?) before it's trusted for buy/sell.
-- See src/nse_data/research/score.py + plans/STOCK_SCORE_PLAN.md.

CREATE TABLE IF NOT EXISTS stock_score (
    symbol         TEXT NOT NULL,
    computed_date  TEXT NOT NULL,        -- YYYY-MM-DD (IST) — one snapshot/day
    score          REAL NOT NULL,        -- [0,1], 0.5 = neutral
    components_json TEXT,                -- {component: signed_delta}
    computed_epoch INTEGER,
    PRIMARY KEY (symbol, computed_date)
);

CREATE INDEX IF NOT EXISTS ix_score_date ON stock_score(computed_date, score);
