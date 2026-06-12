"""
Volume Delta on 5-minute bars (FEATURE_CHECKLIST Phase 4, Week 12, task 12.4).

A cheap buy/sell pressure proxy without tick data: sign the bar's volume by its
candle direction (close > open → buying, close < open → selling) and accumulate.

`cum_vol_delta` is **session-anchored** (resets at each IST trading day), same
grouping as VWAP. It used to be a running sum over the orchestrator's read
window, which made the stored value depend on HOW it was computed — a year-long
backfill produced a year-long cumsum while the every-minute incremental pass
produced a short-window one. Anchoring at the session open makes the value
deterministic and gives it the meaning consumers assume ("net buy/sell pressure
so far today"). Recomputed every minute (cadence=intraday).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ...webcore.config import IST_OFFSET
from ..base import Indicator

_SESSION_SECS = 86_400


class VolumeDelta(Indicator):
    name = "volume_delta_5m"
    table = "indicator_volume_delta_5m"
    pk_cols = ("symbol", "ts")
    output_columns = ("vol_delta", "cum_vol_delta")
    # Session-cumulative like VWAP: the read window must reach back to the
    # current bar's 09:15 open. A full NSE session is 75 five-min bars; 78
    # guarantees the open is in range for the latest possible new bar.
    min_history = 78
    pane = "oscillator"
    cadence = "intraday"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        if ohlcv.empty:
            result["vol_delta"] = result["cum_vol_delta"] = pd.NA
            return result
        direction = np.sign(ohlcv["close"] - ohlcv["open"])
        delta = direction * ohlcv["volume"].fillna(0.0)
        result["vol_delta"] = delta
        # Session key = IST calendar day (same construction as vwap_intraday):
        # the cumulative pressure restarts at each session open.
        session = (ohlcv.index.to_numpy() + IST_OFFSET) // _SESSION_SECS
        result["cum_vol_delta"] = delta.groupby(session).cumsum()
        return result
