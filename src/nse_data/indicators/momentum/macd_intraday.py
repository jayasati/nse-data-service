"""
MACD (12/26/9) on 5-minute bars — recomputed every minute during the session.

Same math as the EOD daily MACD; only cadence and storage PK differ.
The orchestrator routes by `cadence="intraday"` to read merged historical +
live 5-min OHLCV instead of bhavcopy daily bars.

Each bar's value is "live" until 5 minutes have elapsed since `ts` — during
that window the orchestrator overwrites the row on each minute pass via
INSERT OR REPLACE so the in-progress 5-min candle keeps updating.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_FAST = 12
_SLOW = 26
_SIGNAL = 9


class MacdIntraday(Indicator):
    name = "macd_5m"
    table = "indicator_macd_5m"
    pk_cols = ("symbol", "ts")
    output_columns = ("macd", "macd_signal", "macd_hist")
    # 3× the slow window is plenty of EMA settling time. At 5-min bars that's
    # ~6.5 hours — comfortably bridges a single overnight gap (NSE session is
    # 6h 15m), so morning prints have warm-up from yesterday's tail.
    min_history = _SLOW * 3
    pane = "oscillator"
    cadence = "intraday"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        raw = ta.macd(ohlcv["close"], fast=_FAST, slow=_SLOW, signal=_SIGNAL)
        if raw is None:
            result["macd"] = result["macd_signal"] = result["macd_hist"] = pd.NA
            return result
        result["macd"]        = raw[f"MACD_{_FAST}_{_SLOW}_{_SIGNAL}"]
        result["macd_signal"] = raw[f"MACDs_{_FAST}_{_SLOW}_{_SIGNAL}"]
        result["macd_hist"]   = raw[f"MACDh_{_FAST}_{_SLOW}_{_SIGNAL}"]
        return result
