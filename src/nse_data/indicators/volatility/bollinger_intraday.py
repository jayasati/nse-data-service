"""
Bollinger Bands (20, 2) on 5-minute bars.

The daily bands live in `indicator_eod` (EodFullSet, with width + squeeze);
this is the intraday counterpart for the 1D chart view — band rides on the
price axis, touches/walks read as the usual mean-reversion vs band-walk
distinction. Mid band = 20-bar SMA.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_LEN, _STD = 20, 2.0


class BollingerIntraday(Indicator):
    name = "bb_5m"
    table = "indicator_bb_5m"
    pk_cols = ("symbol", "ts")
    output_columns = ("bb_upper", "bb_mid", "bb_lower")
    min_history = _LEN * 3
    pane = "overlay"
    cadence = "intraday"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        bb = ta.bbands(ohlcv["close"], length=_LEN, std=_STD)
        if bb is None or bb.empty:
            result["bb_upper"] = result["bb_mid"] = result["bb_lower"] = pd.NA
            return result
        sfx = f"_{_LEN}_{_STD}"
        result["bb_upper"] = bb[f"BBU{sfx}"]
        result["bb_mid"] = bb[f"BBM{sfx}"]
        result["bb_lower"] = bb[f"BBL{sfx}"]
        return result
