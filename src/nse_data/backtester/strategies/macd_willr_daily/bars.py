"""Read daily OHLCV for one symbol from raw_bhavcopy_cm.

Thin wrapper over `indicators/ohlcv.py:read_ohlcv` that adds an `end_date`
filter (the indicator read path only needs `since_date`) and ensures the
returned DataFrame has the OHLCV columns numeric (raw bhavcopy stores them
as numbers already, but type-coerce defensively).

The index is the TEXT date 'YYYY-MM-DD'. Engine treats each row as one bar.
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from ....indicators.ohlcv import read_ohlcv


def read_daily_bars(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    series: str = "EQ",
) -> pd.DataFrame:
    """Daily OHLCV for `symbol`, indexed by 'YYYY-MM-DD' ascending.

    `start_date` / `end_date` are inclusive. The underlying `read_ohlcv` has
    only a `since_date` filter; we apply `end_date` here in pandas.
    """
    df = read_ohlcv(conn, symbol, since_date=start_date, series=series)
    if df.empty:
        return df
    if end_date is not None:
        df = df.loc[df.index <= end_date]
    # Ensure dtype consistency — bhavcopy stores REAL but pandas can pick up object.
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df
