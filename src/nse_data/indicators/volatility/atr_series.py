"""
ATR(14, Wilder) as a persisted daily series — range expansion/contraction.

The existing `atr.py` helper computes only the latest snapshot for stop
sizing; this persists the curve so the chart can show volatility regime
(expanding ATR = trending conditions, contracting = chop) and `atr_pct`
(ATR as % of close) makes symbols comparable.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_LENGTH = 14


class AtrSeries(Indicator):
    name = "atr"
    table = "indicator_atr"
    pk_cols = ("symbol", "date")
    output_columns = ("atr_14", "atr_pct")
    min_history = _LENGTH * 10      # Wilder smoothing → continuous-series parity
    pane = "oscillator"
    cadence = "eod"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        raw = ta.atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], length=_LENGTH)
        if raw is None:
            result["atr_14"] = result["atr_pct"] = pd.NA
            return result
        result["atr_14"] = raw
        result["atr_pct"] = raw / ohlcv["close"] * 100.0
        return result


class AtrIntraday(AtrSeries):
    """The same ATR(14) on 5-minute bars (intraday stop/target sizing)."""

    name = "atr_5m"
    table = "indicator_atr_5m"
    pk_cols = ("symbol", "ts")
    cadence = "intraday"
