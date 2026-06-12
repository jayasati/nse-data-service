"""
EMA 20 / 50 / 200 on 5-minute bars — recomputed every minute during the session.

Same trend set as the daily EMAs, at intraday cadence: 20-EMA tracks the
session's momentum leg, 50-EMA the day's broader drift, 200-EMA the multi-day
regime anchor. The orchestrator counts `min_history` in BARS across sessions,
so values are continuous over the overnight gap (TradingView parity), not
re-anchored at the open.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_FAST, _SLOW, _REGIME = 20, 50, 200


class EmaIntraday(Indicator):
    name = "ema_5m"
    table = "indicator_ema_5m"
    pk_cols = ("symbol", "ts")
    output_columns = ("ema_20", "ema_50", "ema_200")
    # 6× the longest span — same residual-seed-error policy as before
    # (e^-6 ≈ 0.25% of any seed difference left at the printed edge).
    min_history = _REGIME * 6
    pane = "overlay"
    cadence = "intraday"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        result["ema_20"] = ta.ema(ohlcv["close"], length=_FAST)
        result["ema_50"] = ta.ema(ohlcv["close"], length=_SLOW)
        result["ema_200"] = ta.ema(ohlcv["close"], length=_REGIME)
        return result
