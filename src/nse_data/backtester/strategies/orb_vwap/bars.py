"""5-minute OHLCV reads for the ORB strategy, grouped by IST session.

Mirrors bb_ema9_30m/bars.py but at a 5-min bucket. The 15-minute IST open
offset is a whole multiple of 5 min, so the default resample origin already
lands buckets on the 09:15/09:20/… grid (the 30m reader needed an explicit
origin; 5m does not). A `session` column (IST date) is attached so the engine
can iterate one trading day at a time.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import cast

import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
_BUCKET = "5min"


def read_intraday_5m(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """5-min OHLCV for `symbol`, ascending, with `session` (IST date) column."""
    since = _ist_open(start_date) if start_date else None
    until = _ist_close(end_date) if end_date else None

    sql = ("SELECT ts, open, high, low, close, volume FROM raw_intraday_candles "
           "WHERE symbol = ? AND interval = 'minute'")
    params: list = [symbol]
    if since is not None:
        sql += " AND ts >= ?"; params.append(since)
    if until is not None:
        sql += " AND ts <= ?"; params.append(until)
    sql += " ORDER BY ts ASC"
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return _empty()
    return _resample(rows)


def _ist_open(d: str) -> int:
    dt = datetime.fromisoformat(d).replace(tzinfo=IST) + timedelta(hours=9, minutes=15)
    return int(dt.astimezone(timezone.utc).timestamp())


def _ist_close(d: str) -> int:
    dt = datetime.fromisoformat(d).replace(tzinfo=IST) + timedelta(hours=15, minutes=30)
    return int(dt.astimezone(timezone.utc).timestamp())


def _resample(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=pd.Index(["ts", "open", "high", "low", "close", "volume"]))
    df.index = pd.to_datetime(df["ts"], unit="s", utc=True)
    df = df.drop(columns=["ts"])
    out = cast(pd.DataFrame, df.resample(_BUCKET).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }))
    out = out[pd.notna(out["close"])]
    # IST wall-clock for session grouping + intraday windowing.
    ist_index = cast(pd.DatetimeIndex, out.index).tz_convert(IST)
    out = out.copy()
    out["session"] = ist_index.date
    out["ist_minutes"] = ist_index.hour * 60 + ist_index.minute   # minutes since midnight
    out.index = (out.index.astype("int64") // 1_000_000_000).astype("int64")
    out.index.name = "ts"
    # Regular session only (09:15–15:30 IST).
    return cast(pd.DataFrame, out[(out["ist_minutes"] >= 555) & (out["ist_minutes"] <= 930)])


def _empty() -> pd.DataFrame:
    cols = ["open", "high", "low", "close", "volume", "session", "ist_minutes"]
    return pd.DataFrame(columns=pd.Index(cols)).rename_axis("ts")
