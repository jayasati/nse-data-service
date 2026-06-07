"""
Volume Delta on 5-minute bars (FEATURE_CHECKLIST Phase 4, Week 12, task 12.4).

A cheap buy/sell pressure proxy without tick data: sign the bar's volume by its
candle direction (close > open → buying, close < open → selling) and accumulate.
`cum_vol_delta` is a running sum over the read window (an approximation of the
session's net pressure; not session-anchored — the orchestrator's bridge window
spans the recent session tail). Recomputed every minute (cadence=intraday).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..base import Indicator


class VolumeDelta(Indicator):
    name = "volume_delta_5m"
    table = "indicator_volume_delta_5m"
    pk_cols = ("symbol", "ts")
    output_columns = ("vol_delta", "cum_vol_delta")
    min_history = 1
    pane = "oscillator"
    cadence = "intraday"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        direction = np.sign(ohlcv["close"] - ohlcv["open"])
        delta = direction * ohlcv["volume"]
        result["vol_delta"] = delta
        result["cum_vol_delta"] = delta.cumsum()
        return result
