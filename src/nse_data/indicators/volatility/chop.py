"""
Choppiness Index (14) — is the market trending or ranging?

    chop = 100 · log10( ΣTR(n) / (maxHigh(n) − minLow(n)) ) / log10(n)

Range 0–100. High values (>61.8) mean the path wandered far relative to the
net range — chop; low values (<38.2) mean directional movement — trend. It is
a pure rolling-window statistic (no recursion), so values are exact once the
window is full; `min_history` only needs the window plus the prior close for
the first bar's true range.

Both cadences in one file, same shape as AtrSeries/AtrIntraday.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_LENGTH = 14


class ChopEod(Indicator):
    name = "chop"
    table = "indicator_chop"
    pk_cols = ("symbol", "date")
    output_columns = ("chop_14",)
    min_history = _LENGTH * 3
    pane = "oscillator"
    cadence = "eod"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        raw = ta.chop(ohlcv["high"], ohlcv["low"], ohlcv["close"], length=_LENGTH)
        result["chop_14"] = pd.NA if raw is None else raw
        return result


class ChopIntraday(ChopEod):
    """The same Choppiness Index on 5-minute bars."""

    name = "chop_5m"
    table = "indicator_chop_5m"
    pk_cols = ("symbol", "ts")
    cadence = "intraday"
