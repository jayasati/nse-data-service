"""
Read end-of-day OHLCV for one symbol from raw_bhavcopy_cm.

This is the sole input source for daily indicators. Series is filtered to
'EQ' by default (equities) — bhavcopy stores bonds and other instruments
under the same SYMBOL but different series, and we never want to mix those
into the indicator stack.
"""

from __future__ import annotations

import sqlite3

import pandas as pd

_OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")


def read_ohlcv(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    since_date: str | None = None,
    series: str = "EQ",
) -> pd.DataFrame:
    """
    Return OHLCV for `symbol` from raw_bhavcopy_cm, indexed by `date` ascending.

    since_date: if given, only rows with date >= since_date (inclusive) are
        returned. The orchestrator passes this to limit history to
        (last_computed_date - min_history) bars, supporting incremental compute.
    """
    sql = (
        "SELECT date, open, high, low, close, volume "
        "FROM raw_bhavcopy_cm "
        "WHERE symbol = ? AND series = ?"
    )
    params: list = [symbol, series]
    if since_date is not None:
        sql += " AND date >= ?"
        params.append(since_date)
    sql += " ORDER BY date ASC"

    rows = conn.execute(sql, params).fetchall()
    columns = ["date", *_OHLCV_COLUMNS]
    df = pd.DataFrame(rows, columns=pd.Index(columns))
    return df.set_index("date")
