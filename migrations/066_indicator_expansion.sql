-- Indicator expansion: the full per-symbol toolkit on both cadences.
--
-- Fills the gaps in the 12-indicator set (VWAP/Volume/Structure/CPR/OI/RS/
-- EMA/Supertrend/RSI/Bollinger/MACD/ATR): EMA 20/50 series, persisted ATR,
-- intraday Bollinger + relative volume, CPR (central pivot range), market
-- structure (swing highs/lows + HH-HL/LH-LL state), an RS line vs NIFTYBEES,
-- and a daily OI series with buildup classification. Same shapes as the
-- existing indicator tables: (symbol, date) for EOD, (symbol, ts) for 5-min.

CREATE TABLE IF NOT EXISTS indicator_ema (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    ema_20 REAL, ema_50 REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS indicator_ema_5m (
    symbol TEXT NOT NULL, ts INTEGER NOT NULL,
    ema_20 REAL, ema_50 REAL,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS indicator_atr (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    atr_14 REAL, atr_pct REAL,            -- atr as % of close: gap/stop sizing
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS indicator_atr_5m (
    symbol TEXT NOT NULL, ts INTEGER NOT NULL,
    atr_14 REAL, atr_pct REAL,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS indicator_bb_5m (
    symbol TEXT NOT NULL, ts INTEGER NOT NULL,
    bb_upper REAL, bb_mid REAL, bb_lower REAL,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS indicator_rvol_5m (
    symbol TEXT NOT NULL, ts INTEGER NOT NULL,
    vol_sma20 REAL, rvol REAL,            -- this bar's volume / 20-bar avg
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS indicator_cpr (
    symbol TEXT NOT NULL, date TEXT NOT NULL,  -- levels valid FOR this date
    cpr_pivot REAL, cpr_tc REAL, cpr_bc REAL,
    cpr_width_pct REAL,                        -- narrow CPR = trending-day setup
    r1 REAL, s1 REAL, r2 REAL, s2 REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS indicator_structure (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    swing_high REAL, swing_low REAL,           -- last CONFIRMED swing levels
    structure INTEGER,                         -- 1 HH+HL up / -1 LH+LL down / 0 mixed
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS indicator_structure_5m (
    symbol TEXT NOT NULL, ts INTEGER NOT NULL,
    swing_high REAL, swing_low REAL,
    structure INTEGER,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS indicator_rs (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    rs_line REAL,                              -- 100 × close / NIFTYBEES close
    rs_sma20 REAL,                             -- its 20-day trend
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS indicator_oi (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    oi REAL, oi_change_pct REAL,
    oi_buildup INTEGER,   -- 1 long-buildup / -1 short-buildup / 2 short-covering / -2 long-unwinding / 0
    PRIMARY KEY (symbol, date)
);
