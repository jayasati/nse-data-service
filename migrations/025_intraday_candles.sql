


-- Historical intraday OHLCV candles, backfilled from a broker API
-- (Zerodha Kite Connect by default; see src/nse_data/brokers/kite.py).
--
-- NSE's free feeds do not publish historical minute data, so this table is
-- populated by scripts/backfill_intraday.py against a licensed source. Our
-- live 1-min feed (raw_equity_quotes) supplies *today*; this table supplies
-- the past. The dashboard history endpoint merges both.
--
-- ts is the bar-start time as Unix epoch SECONDS in UTC (kite returns IST
-- datetimes; we store the absolute epoch and add the IST display offset when
-- serving, matching the live-feed convention).

CREATE TABLE IF NOT EXISTS raw_intraday_candles (
    symbol    TEXT    NOT NULL,
    interval  TEXT    NOT NULL,            -- kite naming: 'minute', '5minute', ...
    ts        INTEGER NOT NULL,            -- epoch seconds (UTC) of bar start
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    volume    INTEGER,
    source    TEXT    NOT NULL DEFAULT 'kite',
    PRIMARY KEY (symbol, interval, ts)
);

CREATE INDEX IF NOT EXISTS idx_intraday_lookup
    ON raw_intraday_candles(symbol, interval, ts);
