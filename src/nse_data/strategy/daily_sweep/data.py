"""Multi-timeframe candle access for the Daily Sweep strategy.

ONE source — `raw_intraday_candles`. Coverage differs by instrument: stocks carry native `day`
+ `5minute`; indices (NIFTY/BANKNIFTY/FINNIFTY) carry only `minute`. So each timeframe falls
back to RESAMPLING the finest available interval, and both instrument types work uniformly:

    daily  = native 'day'  → else resample minute/5minute to 1 calendar day
    5m     = native '5minute' → else resample 'minute'
    1h     = always resampled from 5m, anchored to the 09:15 IST open (NSE files no native 1h)

All frames are IST-indexed so the session filter (Step 8) + multi-TF alignment use wall-clock.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

IST = "Asia/Kolkata"
_OHLCV = ["open", "high", "low", "close", "volume"]
_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def _read(conn: sqlite3.Connection, symbol: str, interval: str,
          start: str | None, end: str | None) -> pd.DataFrame:
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM raw_intraday_candles "
        "WHERE symbol=? AND interval=? ORDER BY ts", (symbol, interval)).fetchall()
    if not rows:
        return pd.DataFrame(columns=_OHLCV)
    df = pd.DataFrame(rows, columns=["ts", *_OHLCV])
    df.index = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(IST)
    df = df.drop(columns="ts")
    for c in _OHLCV:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if start:
        df = df.loc[df.index >= pd.Timestamp(start, tz=IST)]
    if end:
        df = df.loc[df.index <= pd.Timestamp(end, tz=IST)]
    return df


def _resample(df: pd.DataFrame, rule: str, origin=None) -> pd.DataFrame:
    if df.empty:
        return df
    kw = {"origin": origin} if origin is not None else {}
    return df.resample(rule, **kw).agg(_AGG).dropna(subset=["open"])


def _finest(conn, symbol, start, end) -> pd.DataFrame:
    """The finest available bars: native 5minute, else minute."""
    m5 = _read(conn, symbol, "5minute", start, end)
    return m5 if not m5.empty else _read(conn, symbol, "minute", start, end)


def read_daily(conn, symbol, *, start=None, end=None) -> pd.DataFrame:
    df = _read(conn, symbol, "day", start, end)
    if not df.empty:
        return df
    return _resample(_finest(conn, symbol, start, end), "1D")   # indices: minute → daily


def read_5m(conn, symbol, *, start=None, end=None) -> pd.DataFrame:
    df = _read(conn, symbol, "5minute", start, end)
    if not df.empty:
        return df
    return _resample(_read(conn, symbol, "minute", start, end), "5min")


def read_1h(conn, symbol, *, start=None, end=None) -> pd.DataFrame:
    """1-hour bars from 5m, anchored to 09:15 IST so a bar = 09:15–10:15 etc."""
    m5 = read_5m(conn, symbol, start=start, end=end)
    if m5.empty:
        return m5
    origin = m5.index[0].normalize() + pd.Timedelta("9h15min")
    return _resample(m5, "60min", origin=origin)
