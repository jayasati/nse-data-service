"""
Supertrend on 5-minute bars — recomputed every minute during the session.

Period 10, multiplier 3.0 — the TradingView/industry default. We compute it
with our own ``supertrend_calc`` rather than pandas-ta, because pandas-ta's
SuperTrend disagrees with TradingView materially (wrong band side, ~10× the
flips — see supertrend_calc docstring). A flip in `supertrend_dir` is the
primary intraday "regime changed" signal.

The band ratchets recursively, so its level depends on history depth:
`min_history` is generous (and the orchestrator counts it in BARS across
sessions) so the band agrees with a continuous computation, not one
re-anchored at today's open.
"""

from __future__ import annotations

import pandas as pd

from ..base import Indicator
from .supertrend_calc import supertrend

_LEN, _MULT = 10, 3.0


class SupertrendIntraday(Indicator):
    name = "supertrend_5m"
    table = "indicator_supertrend_5m"
    pk_cols = ("symbol", "ts")
    output_columns = ("supertrend", "supertrend_dir")
    min_history = _LEN * 20      # band converges to the continuous series
    cadence = "intraday"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        return supertrend(ohlcv["high"], ohlcv["low"], ohlcv["close"],
                          length=_LEN, multiplier=_MULT)
