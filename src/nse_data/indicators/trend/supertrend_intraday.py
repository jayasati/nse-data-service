"""
Supertrend on 5-minute bars — recomputed every minute during the session.

Period 10, multiplier 2.0 (same params as the daily Supertrend). A flip in
`supertrend_dir` is the primary intraday "regime changed" signal. Same library
(pandas-ta-classic) as everywhere else for parity.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_LEN, _MULT = 10, 2.0


class SupertrendIntraday(Indicator):
    name = "supertrend_5m"
    table = "indicator_supertrend_5m"
    pk_cols = ("symbol", "ts")
    output_columns = ("supertrend", "supertrend_dir")
    min_history = _LEN * 3        # settle the ATR band before first print
    cadence = "intraday"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        st = ta.supertrend(
            ohlcv["high"], ohlcv["low"], ohlcv["close"],
            length=_LEN, multiplier=_MULT,
        )
        if st is None or st.empty:
            result["supertrend"] = pd.NA
            result["supertrend_dir"] = pd.NA
            return result
        sfx = f"_{_LEN}_{_MULT}"
        result["supertrend"] = st[f"SUPERT{sfx}"]
        result["supertrend_dir"] = st[f"SUPERTd{sfx}"]
        return result
