"""
Nightly indicator compute job.

Per-stock incremental: for each (symbol, indicator) we look up the last
date already in the indicator's table, then read just enough OHLCV history
to recompute from there forward — `indicator.min_history` bars of lookback
plus every bar since the last write. The indicator runs on that slice and
we persist only the rows we did not already have.

Why pull lookback even though we already wrote those dates: rolling-window
indicators (SMA, EMA, ATR, ...) need the prior window to produce the next
value. We re-run the math over the lookback but only insert the *new* rows,
which keeps the write cost proportional to fresh bars and avoids touching
historic indicator rows that haven't changed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, cast

import pandas as pd

from .base import Indicator
from .ohlcv import read_ohlcv
from .registry import INDICATORS
from .writer import last_computed_date, write_indicator


@dataclass(frozen=True)
class ComputeResult:
    symbol: str
    indicator: str
    rows_written: int


def compute_for_symbol(
    conn: sqlite3.Connection,
    indicator: Indicator,
    symbol: str,
    *,
    series: str = "EQ",
) -> ComputeResult:
    """
    Run one indicator for one symbol, incrementally. Returns the row count
    actually written (0 if up to date or insufficient history).
    """
    last_date = last_computed_date(conn, indicator, symbol)
    since = _lookback_cutoff(conn, symbol, last_date, indicator.min_history, series)

    ohlcv = read_ohlcv(conn, symbol, since_date=since, series=series)
    if ohlcv.empty:
        return ComputeResult(symbol, indicator.name, 0)

    values = indicator.compute(ohlcv)
    if last_date is not None:
        # Drop rows we already persisted — incremental write only.
        values = cast(pd.DataFrame, values.loc[values.index > last_date])

    rows = write_indicator(conn, indicator, symbol, values)
    return ComputeResult(symbol, indicator.name, rows)


def run_all(
    conn: sqlite3.Connection,
    symbols: Iterable[str],
    *,
    indicators: Iterable[Indicator] = INDICATORS,
    series: str = "EQ",
) -> list[ComputeResult]:
    """Run every registered indicator against every symbol. Sequential."""
    results: list[ComputeResult] = []
    for symbol in symbols:
        for ind in indicators:
            results.append(compute_for_symbol(conn, ind, symbol, series=series))
    return results


def _lookback_cutoff(
    conn: sqlite3.Connection,
    symbol: str,
    last_date: str | None,
    min_history: int,
    series: str,
) -> str | None:
    """
    Choose the earliest OHLCV date to load.

    First run (no last_date): None — read full history. The indicator will
    emit NaN for the first `min_history - 1` rows, which the writer drops.

    Incremental run: load `min_history` bars before the first un-written
    bar so rolling windows have their warm-up data. We compute that bound
    by looking up the (min_history)-th most-recent bhavcopy date strictly
    older than `last_date`. SQLite + index makes this cheap.
    """
    if last_date is None:
        return None

    row = conn.execute(
        """
        SELECT date FROM raw_bhavcopy_cm
        WHERE symbol = ? AND series = ? AND date <= ?
        ORDER BY date DESC
        LIMIT 1 OFFSET ?
        """,
        (symbol, series, last_date, min_history - 1),
    ).fetchone()

    # If we don't have `min_history` bars of history before `last_date`,
    # fall back to full read — the indicator decides whether it can score.
    return row[0] if row else None
