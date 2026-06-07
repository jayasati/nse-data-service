-- Per-symbol fundamentals + quality score (FEATURE_CHECKLIST Phase 4, Week 14).
--
-- Computed nightly (fundamentals/quality_score.py) from the screener / quote
-- metadata / shareholding feeds. `quality_score` (0–100) gates and nudges the
-- signal pipeline; it is NULL when no fundamental components are available for a
-- symbol (so a data gap never reads as a 0 / low-quality kill).

CREATE TABLE IF NOT EXISTS stock_fundamentals (
    symbol             TEXT PRIMARY KEY,
    quality_score      REAL,
    revenue_growth_yoy REAL,
    roe                REAL,
    roce               REAL,
    debt_equity        REAL,
    pe_ratio           REAL,
    market_cap         REAL,
    promoter_holding   REAL,
    promoter_pledge    REAL,
    loss_making        INTEGER,   -- 1 / 0
    high_debt          INTEGER,   -- D/E > 2
    updated_date       TEXT
);
