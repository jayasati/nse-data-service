"""
Simple Moving Average — 20, 50, and 200-bar windows on daily closes.

Three windows in one indicator because they share OHLCV input and target
table (indicator_sma); splitting them would triple read and write cost for
no analytical benefit. min_history matches the longest window so the
orchestrator pulls enough lookback to score the 200-bar value too.

We use pandas-ta-classic (the numpy-2 compatible fork of pandas-ta) for math
consistency with the rest of the indicator stack, even though SMA is trivial
— it keeps every indicator on the same engine and avoids drift between
hand-rolled and library formulas during reviews.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_WINDOWS: tuple[int, ...] = (20, 50, 200)


class SimpleMovingAverage(Indicator):
    name = "sma"
    table = "indicator_sma"
    pk_cols = ("symbol", "date")
    output_columns = tuple(f"sma_{w}" for w in _WINDOWS)
    min_history = max(_WINDOWS)

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        close = ohlcv["close"]
        result = pd.DataFrame(index=ohlcv.index)
        for window in _WINDOWS:
            result[f"sma_{window}"] = ta.sma(close, length=window)
        return result
