"""
Relative Strength Index (14-period, Wilder smoothing) on daily closes.

Wilder's RSI: average gain and average loss over `length` bars, smoothed via
Wilder's EMA (alpha = 1/length). RS = avg_gain / avg_loss; RSI = 100 −
100/(1+RS). Range 0–100; values >70 conventionally overbought, <30 oversold.

We delegate the math to pandas-ta-classic so the formula stays consistent
with the rest of the stack (and matches TradingView/Groww's defaults).
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_LENGTH = 14


class RelativeStrengthIndex(Indicator):
    name = "rsi"
    table = "indicator_rsi"
    pk_cols = ("symbol", "date")
    output_columns = (f"rsi_{_LENGTH}",)
    # Wilder smoothing converges to a stable value over a few multiples of
    # `length`. 14 bars technically yields a value, but the first few are
    # noisy because the smoothing memory is short — pull a generous lookback
    # so incremental runs produce clean values from the start.
    min_history = _LENGTH * 3
    pane = "oscillator"
    cadence = "eod"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        # pandas-ta-classic returns None on too-short input — emit NaN so the
        # writer drops the row (rather than crashing on `None[col]`).
        raw = ta.rsi(ohlcv["close"], length=_LENGTH)
        result[f"rsi_{_LENGTH}"] = pd.NA if raw is None else raw
        return result
