"""
EMA 20 / EMA 50 on 5-minute bars — recomputed every minute during the session.

Same trend pair as the daily EMAs, at intraday cadence: 20-EMA tracks the
session's momentum leg, 50-EMA the day's broader drift. The orchestrator
counts `min_history` in BARS across sessions, so values are continuous over
the overnight gap (TradingView parity), not re-anchored at the open.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_FAST, _SLOW = 20, 50


class EmaIntraday(Indicator):
    name = "ema_5m"
    table = "indicator_ema_5m"
    pk_cols = ("symbol", "ts")
    output_columns = ("ema_20", "ema_50")
    min_history = _SLOW * 6
    pane = "overlay"
    cadence = "intraday"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        result["ema_20"] = ta.ema(ohlcv["close"], length=_FAST)
        result["ema_50"] = ta.ema(ohlcv["close"], length=_SLOW)
        return result
