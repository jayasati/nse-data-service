"""30-minute OHLCV reads for the backtester.

This module deliberately does NOT extend `indicators/intraday_ohlcv.py`. That
file is on the live hot path (re-called every 60s by the live indicator job);
optimizing it for "today + tiny lookback" is the right tradeoff there. The
backtester needs months of history per symbol in one shot and merges no live
feed — different workload, different code path.

## IST bucket alignment

`pd.resample("30min")` defaults to `origin='start_day'` (UTC midnight). With
NSE's 09:15 IST open and a 30-minute grid, UTC-midnight buckets are off by
15 minutes: bars would start at 09:00 / 09:30 IST instead of 09:15 / 09:45,
and the open auction print would land in a bucket that mostly covers the
pre-open silent window.

The fix is an explicit `origin=` anchored to 03:45 UTC = 09:15 IST. Since
the IST offset (+19800s) is a whole multiple of 1800s, the resulting bucket
edges are exactly the NSE 30-min session grid:
    09:15 09:45 10:15 10:45 11:15 11:45 12:15 12:45 13:15 13:45 14:15 14:45 15:15

The 5-min resampler in `intraday_ohlcv.py` got away with the default origin
only because 15 % 5 == 0. At 30m it does not.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from typing import cast

import pandas as pd

# IST anchor for the 30-min grid. Don't change this without re-running the
# alignment test in tests/backtester/test_bars_30m_alignment.py.
_IST_ORIGIN: pd.Timestamp = cast(pd.Timestamp, pd.Timestamp("1970-01-01 03:45:00", tz="UTC"))
_BUCKET = "30min"

_OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
IST = timezone(timedelta(hours=5, minutes=30))


def read_intraday_30m(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """30-min OHLCV for `symbol`, indexed by `ts` (UTC epoch seconds) ascending.

    `start_date` / `end_date` are inclusive IST trading days ("YYYY-MM-DD").
    Internally converted to a UTC ts range covering 09:15 IST to 15:30 IST
    of those dates, with a one-bar pad on the left for the gap calculation.
    """
    since_ts = _ist_day_to_utc_open(start_date) if start_date else None
    until_ts = _ist_day_to_utc_close(end_date) if end_date else None

    rows = _read_minutes(conn, symbol, since_ts, until_ts)
    if not rows:
        return _empty()
    return _resample(rows)


def _ist_day_to_utc_open(d: str) -> int:
    """Epoch seconds for 09:15 IST on `d` (the first 30-min bar of that day)."""
    dt = datetime.fromisoformat(d).replace(tzinfo=IST) + timedelta(hours=9, minutes=15)
    return int(dt.astimezone(timezone.utc).timestamp())


def _ist_day_to_utc_close(d: str) -> int:
    """Epoch seconds for 15:30 IST on `d` (end of regular session)."""
    dt = datetime.fromisoformat(d).replace(tzinfo=IST) + timedelta(hours=15, minutes=30)
    return int(dt.astimezone(timezone.utc).timestamp())


def _read_minutes(
    conn: sqlite3.Connection,
    symbol: str,
    since_ts: int | None,
    until_ts: int | None,
) -> list[tuple]:
    sql = (
        "SELECT ts, open, high, low, close, volume "
        "FROM raw_intraday_candles "
        "WHERE symbol = ? AND interval = 'minute'"
    )
    params: list = [symbol]
    if since_ts is not None:
        sql += " AND ts >= ?"
        params.append(since_ts)
    if until_ts is not None:
        sql += " AND ts <= ?"
        params.append(until_ts)
    sql += " ORDER BY ts ASC"
    return conn.execute(sql, params).fetchall()


def _resample(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(
        rows, columns=pd.Index(["ts", "open", "high", "low", "close", "volume"]),
    )
    df.index = pd.to_datetime(df["ts"], unit="s", utc=True)
    df = df.drop(columns=["ts"])

    out = cast(pd.DataFrame, (
        df.resample(_BUCKET, origin=_IST_ORIGIN)
        .agg({
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        })
    ))
    out = out[pd.notna(out["close"])]

    # Strip pre-/post-market degenerate bars (auction-only or empty resample
    # cells that survive dropna because some symbols print zero-volume ticks).
    flat = (
        (out["open"] == out["high"])
        & (out["high"] == out["low"])
        & (out["low"] == out["close"])
        & (out["volume"] == 0)
    )
    out = cast(pd.DataFrame, out[~flat])

    out.index = (out.index.astype("int64") // 1_000_000_000).astype("int64")
    out.index.name = "ts"
    return out


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=pd.Index(list(_OHLCV_COLUMNS))).rename_axis("ts")
