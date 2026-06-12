-- Intraday ADX/DI (Wilder, 14) on 5-minute bars.
-- Recomputed every minute during market hours by the live indicator scheduler,
-- same cadence/shape as indicator_rsi_5m / indicator_macd_5m.
--
-- The EOD ADX already lives in indicator_eod (eod_full set); this is its
-- intraday counterpart so the dashboard/bot stop recomputing ADX client-side
-- from whatever bars the chart loaded.
--
-- `ts` is UTC epoch seconds at the 5-min bar start (matches raw_intraday_candles).
-- Three series in one row: the canonical reads (trend strength >25, DI cross)
-- need all of them together.
--
-- Retention: rolling 30 calendar days. See src/nse_data/indicators/retention.py.
-- No secondary indexes: the (symbol, ts) PK covers the per-symbol reads
-- (see 031_drop_redundant_intraday_indexes.sql for the rationale).

CREATE TABLE IF NOT EXISTS indicator_adx_5m (
    symbol   TEXT    NOT NULL,
    ts       INTEGER NOT NULL,
    adx      REAL,
    di_plus  REAL,
    di_minus REAL,
    PRIMARY KEY (symbol, ts)
);
