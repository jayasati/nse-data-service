"""
Supertrend on 5-minute bars — recomputed every minute during the session.

Period 10, multiplier 3.0 — the TradingView/industry default, so the chart
matches what every other terminal shows for "Supertrend 10,3" (it ran at 2.0
until 2026-06, which hugged price visibly tighter than TV). A flip in
`supertrend_dir` is the primary intraday "regime changed" signal. Same library
(pandas-ta-classic) as everywhere else for parity.

Supertrend's band ratchets recursively, so its level depends on history depth:
`min_history` is generous (and the orchestrator counts it in BARS across
sessions) so the band agrees with a continuous computation, not one
re-anchored at today's open. pandas-ta emits 0 (not NaN) during its ATR
warm-up — those rows are masked to NA so the writer drops them instead of
charting zeros.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_LEN, _MULT = 10, 3.0


class SupertrendIntraday(Indicator):
    name = "supertrend_5m"
    table = "indicator_supertrend_5m"
    pk_cols = ("symbol", "ts")
    output_columns = ("supertrend", "supertrend_dir")
    min_history = _LEN * 20      # band converges to the continuous series
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
        band = st[f"SUPERT{sfx}"].mask(st[f"SUPERT{sfx}"] == 0)   # warm-up zeros → NA
        result["supertrend"] = band
        result["supertrend_dir"] = st[f"SUPERTd{sfx}"].where(band.notna())
        return result
