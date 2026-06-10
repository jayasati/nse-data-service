"""
Relative volume on 5-minute bars — is THIS bar's volume unusual?

rvol = bar volume / rolling 20-bar average. >2 on a breakout bar is the
participation confirmation every price signal wants; <0.5 = dry-up. The daily
counterpart (vol_sma20 / volume_ratio) lives in EodFullSet; this is the
intraday read for the 1D chart and the live decision path.

(A rolling-bar baseline deliberately mixes times of day; a time-of-day
baseline — "vs the average 10:15 bar" — is the known sharper upgrade, noted
for later. The rolling version is what most terminals show.)
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_LEN = 20


class RelativeVolumeIntraday(Indicator):
    name = "rvol_5m"
    table = "indicator_rvol_5m"
    pk_cols = ("symbol", "ts")
    output_columns = ("vol_sma20", "rvol")
    min_history = _LEN * 3
    pane = "oscillator"
    cadence = "intraday"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        sma = ta.sma(ohlcv["volume"], length=_LEN)
        result["vol_sma20"] = sma
        result["rvol"] = (ohlcv["volume"] / sma).where(sma > 0)
        return result
