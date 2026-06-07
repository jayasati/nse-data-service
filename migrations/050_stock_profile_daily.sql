-- Daily stock profile (FEATURE_CHECKLIST Phase 4, Week 15, task 15.5).
--
-- One wide row per symbol per session joining every Layer-4 output —
-- fundamentals, delivery, the full EOD indicator set, levels, the end-of-session
-- live tags, and the day's pattern flags. This is the ML training archive
-- (built nightly by profile/builder.py at 19:30).

CREATE TABLE IF NOT EXISTS stock_profile_daily (
    symbol                     TEXT NOT NULL,
    session_date               TEXT NOT NULL,

    -- fundamentals (stock_fundamentals)
    quality_score              REAL,
    revenue_growth_yoy         REAL,
    roe                        REAL,
    roce                       REAL,
    debt_equity                REAL,
    pe_ratio                   REAL,
    market_cap                 REAL,
    promoter_holding           REAL,
    loss_making                INTEGER,
    high_debt                  INTEGER,

    -- delivery (delivery_conviction)
    delivery_ratio             REAL,
    delivery_ratio_5d_avg      REAL,
    delivery_ratio_z_score     REAL,
    delivery_trend             TEXT,
    delivery_conviction_score  REAL,

    -- EOD indicator set (indicator_eod / sma / rsi / macd)
    ema9                       REAL,
    ema21                      REAL,
    bb_upper                   REAL,
    bb_lower                   REAL,
    bb_width                   REAL,
    bb_squeeze                 INTEGER,
    adx                        REAL,
    di_plus                    REAL,
    di_minus                   REAL,
    supertrend                 REAL,
    supertrend_dir             INTEGER,
    obv                        REAL,
    vol_sma20                  REAL,
    volume_ratio               REAL,
    sma_20                     REAL,
    sma_50                     REAL,
    sma_200                    REAL,
    rsi_14                     REAL,
    macd                       REAL,
    macd_signal                REAL,
    macd_hist                  REAL,

    -- levels (indicator_levels)
    pdh                        REAL,
    pdl                        REAL,
    high_52w                   REAL,
    low_52w                    REAL,
    days_since_52w_high        INTEGER,
    range_5d_high              REAL,
    range_5d_low               REAL,
    range_20d_high             REAL,
    range_20d_low              REAL,
    nearest_round_number       REAL,
    round_number_prior_failures INTEGER,
    r1                         REAL,
    r2                         REAL,
    s1                         REAL,
    s2                         REAL,

    -- end-of-session live tags (indicator_live)
    trend_regime               TEXT,
    momentum_state             TEXT,
    price_vs_vwap              TEXT,
    atr_14_daily               REAL,

    -- pattern flags for the day (patterns)
    had_inside_bar             INTEGER,
    had_volume_dryup           INTEGER,
    had_bullish_divergence     INTEGER,
    had_bearish_divergence     INTEGER,
    near_support               INTEGER,
    near_resistance            INTEGER,

    updated_at                 TEXT,
    PRIMARY KEY (symbol, session_date)
);
