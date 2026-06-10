"""
Relative Strength line — the stock vs the market, as a series.

rs_line = 100 × close / NIFTYBEES close. Rising = outperforming the index
regardless of absolute direction (the institutional tell: a stock holding its
RS slope through a market dip is under accumulation). `rs_sma20` smooths it so
slope and crossovers read cleanly. The sector radar ranks *sectors*; this is
the per-stock counterpart the chart shows.

Benchmark: **NIFTYBEES** (the Nifty 50 ETF) rather than the index itself —
it lives in the same bhavcopy table as every stock, so the series aligns by
construction (`raw_indices` only carries live snapshots, not daily history).

This indicator needs a second symbol's series, which the pure
``compute(ohlcv)`` contract can't see — the orchestrator calls ``prepare()``
first, which loads/caches the benchmark closes (refreshed only when a new
bhavcopy date lands, so the nightly loop over thousands of symbols pays one
read, not one per symbol).
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pandas_ta_classic as ta

from .base import Indicator

_BENCHMARK = "NIFTYBEES"
_SMA = 20


class RelativeStrengthLine(Indicator):
    name = "rs"
    table = "indicator_rs"
    pk_cols = ("symbol", "date")
    output_columns = ("rs_line", "rs_sma20")
    min_history = _SMA * 3
    pane = "oscillator"
    cadence = "eod"

    def __init__(self) -> None:
        self._bench: pd.Series | None = None
        self._bench_max_date: str | None = None

    def prepare(self, conn: sqlite3.Connection, symbol: str) -> None:  # noqa: ARG002
        try:
            latest = conn.execute(
                "SELECT MAX(date) FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ'",
                (_BENCHMARK,),
            ).fetchone()
        except sqlite3.OperationalError:
            self._bench = None
            return
        max_date = latest[0] if latest else None
        if max_date is None:
            self._bench = None
            return
        if max_date == self._bench_max_date and self._bench is not None:
            return                      # cache hit — nothing new to load
        rows = conn.execute(
            "SELECT date, close FROM raw_bhavcopy_cm "
            "WHERE symbol=? AND series='EQ' ORDER BY date",
            (_BENCHMARK,),
        ).fetchall()
        self._bench = pd.Series({r[0]: r[1] for r in rows}, dtype="float")
        self._bench_max_date = max_date

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        if self._bench is None or self._bench.empty:
            result["rs_line"] = result["rs_sma20"] = pd.NA
            return result
        bench = self._bench.reindex(ohlcv.index)
        rs = (100.0 * ohlcv["close"] / bench).where(bench > 0)
        result["rs_line"] = rs
        result["rs_sma20"] = ta.sma(rs, length=_SMA)
        return result
