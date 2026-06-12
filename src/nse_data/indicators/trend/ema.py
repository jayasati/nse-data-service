"""
EMA 20 / EMA 50 on daily closes — short- and medium-term trend.

20 EMA = the short-term trend the price respects in a momentum leg; 50 EMA =
the medium-term trend (pullback support in an uptrend). Price above both with
20 > 50 is the classic aligned-trend read. Server-computed so the values match
everywhere (the chart previously approximated EMAs client-side from visible
bars only, which drifts at the left edge).

EMAs are recursive — min_history is generous so the printed value matches a
continuous computation (TradingView parity), same policy as RSI/MACD.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_FAST, _SLOW, _REGIME = 20, 50, 200


class EodEma(Indicator):
    name = "ema"
    table = "indicator_ema"
    pk_cols = ("symbol", "date")
    output_columns = ("ema_20", "ema_50", "ema_200")
    # 6× the longest span; on a shorter backfill the EOD source simply reads
    # everything available and the 200-EMA starts once 200 closes exist.
    min_history = _REGIME * 6
    pane = "overlay"
    cadence = "eod"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        result["ema_20"] = ta.ema(ohlcv["close"], length=_FAST)
        result["ema_50"] = ta.ema(ohlcv["close"], length=_SLOW)
        result["ema_200"] = ta.ema(ohlcv["close"], length=_REGIME)
        return result
