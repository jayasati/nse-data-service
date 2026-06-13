"""
Indicator compute orchestrator (incremental, cadence-aware).

For each (symbol, indicator) the flow is the same regardless of cadence:

    1. Look up the watermark — the indicator's most recent persisted PK time
       value (date string for EOD, epoch seconds for intraday/session).
    2. Compute the *lookback cutoff* — the earliest input bar we need to read
       so the indicator has its `min_history` warm-up plus every new bar
       since the watermark.
    3. Read OHLCV from the cadence's source (bhavcopy / intraday_candles+live
       feed / session-anchored builder).
    4. Run `indicator.compute(ohlcv)` — pure math, no I/O.
    5. Drop rows at or before the watermark, write the rest. Re-emitting the
       warm-up rows would be wasted writes; we only persist new bars.

The cadence-specific I/O is encapsulated in a `_Source` (read + lookback) so
new cadences (e.g. session-anchored pivots) plug in without touching the
orchestrator's main loop.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Iterable, cast

import pandas as pd
import structlog

from .base import Indicator
from .intraday_ohlcv import read_intraday_5m
from .ohlcv import read_ohlcv
from .registry import INDICATORS
from .writer import last_computed_key, write_indicator

_log = structlog.get_logger()


@dataclass(frozen=True)
class ComputeResult:
    symbol: str
    indicator: str
    rows_written: int


# ---------------------------------------------------------------- public API

def compute_for_symbol(
    conn: sqlite3.Connection,
    indicator: Indicator,
    symbol: str,
    *,
    series: str = "EQ",
) -> ComputeResult:
    """Run one indicator for one symbol, incrementally."""
    source = _SOURCES[indicator.cadence]

    # Indicators that need data beyond the symbol's own OHLCV (a benchmark
    # series, an OI feed) load it here — compute() itself stays pure.
    prepare = getattr(indicator, "prepare", None)
    if prepare is not None:
        prepare(conn, symbol)

    watermark = last_computed_key(conn, indicator, symbol)
    since = source.lookback_cutoff(conn, symbol, watermark, indicator.min_history, series)
    ohlcv = source.read(conn, symbol, since=since, series=series)
    if ohlcv.empty:
        return ComputeResult(symbol, indicator.name, 0)

    values = indicator.compute(ohlcv)
    if watermark is not None:
        # Drop rows we already persisted — incremental write only.
        values = cast(pd.DataFrame, values.loc[values.index > watermark])

    rows = write_indicator(conn, indicator, symbol, values)
    return ComputeResult(symbol, indicator.name, rows)


def run_all(
    conn: sqlite3.Connection,
    symbols: Iterable[str],
    *,
    indicators: Iterable[Indicator] = INDICATORS,
    series: str = "EQ",
    cadence: str | None = None,
) -> list[ComputeResult]:
    """Sweep the universe. `cadence` filters which indicators run (None = all)."""
    selected = [i for i in indicators if cadence is None or i.cadence == cadence]
    results: list[ComputeResult] = []
    for symbol in symbols:
        for ind in selected:
            try:
                results.append(compute_for_symbol(conn, ind, symbol, series=series))
            except Exception:
                # One bad symbol (e.g. malformed volume) must not abort the whole
                # universe sweep — log it, record zero rows, carry on.
                _log.exception("indicator_compute_failed", symbol=symbol, indicator=ind.name)
                results.append(ComputeResult(symbol, ind.name, 0))
    return results


# ------------------------------------------------------ cadence-specific I/O

ReadFn = Callable[..., pd.DataFrame]
LookbackFn = Callable[..., Any]


@dataclass(frozen=True)
class _Source:
    """Pluggable I/O for one cadence. Selected by `indicator.cadence`."""
    read: ReadFn
    lookback_cutoff: LookbackFn


def _eod_read(conn, symbol, *, since=None, series="EQ"):
    return read_ohlcv(conn, symbol, since_date=since, series=series)


def _eod_lookback(conn, symbol, watermark, min_history, series):
    """Find the date `min_history` trading-day-rows earlier than `watermark`.

    We can't just subtract calendar days — NSE has holidays and weekends.
    Counting backwards through bhavcopy gives the exact warm-up window.
    """
    if watermark is None:
        return None
    row = conn.execute(
        """
        SELECT date FROM raw_bhavcopy_cm
        WHERE symbol = ? AND series = ? AND date <= ?
        ORDER BY date DESC LIMIT 1 OFFSET ?
        """,
        (symbol, series, watermark, min_history - 1),
    ).fetchone()
    return row[0] if row else None


def _intraday_read(conn, symbol, *, since=None, series="EQ"):
    # `series` ignored — intraday source isn't series-aware.
    return read_intraday_5m(conn, symbol, since_ts=since)


_INTRADAY_BAR_SECS = 300  # 5-min bars


def _intraday_lookback(conn, symbol, watermark, min_history, series):
    """Epoch of the `min_history`-th 5-min bar before the watermark — counted
    in BARS through the backfill, exactly as the EOD path counts trading days.

    Counting wall-clock seconds here was the intraday-accuracy bug: a
    `min_history × 300s` window dies at the overnight gap, so every morning
    the recursive indicators (RSI/MACD/Supertrend) re-seeded from nothing —
    13 NaN bars at the open, then ~3 RSI points of seed error vs the
    continuous series TradingView shows. Counting 1-minute rows bridges
    sessions; wall-clock remains only as the fallback with no backfill."""
    if watermark is None:
        return None
    try:
        row = conn.execute(
            "SELECT MIN(ts) FROM (SELECT ts FROM raw_intraday_candles "
            "WHERE symbol = ? AND interval = 'minute' AND ts < ? "
            "ORDER BY ts DESC LIMIT ?)",
            (symbol, int(watermark), min_history * 5),  # 5 one-min rows per 5-min bar
        ).fetchone()
    except Exception:  # noqa: BLE001 — table absent on a bare DB
        row = None
    if row and row[0] is not None:
        return int(row[0])
    return int(watermark) - min_history * _INTRADAY_BAR_SECS


_SOURCES: dict[str, _Source] = {
    "eod": _Source(read=_eod_read, lookback_cutoff=_eod_lookback),
    "intraday": _Source(read=_intraday_read, lookback_cutoff=_intraday_lookback),
}
