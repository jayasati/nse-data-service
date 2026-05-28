"""
MACD (12/26/9) on daily closes — line, signal, and histogram.

Standard Gerald Appel parameters:
    macd        = EMA(close, 12) − EMA(close, 26)
    macd_signal = EMA(macd, 9)
    macd_hist   = macd − macd_signal

Bullish/bearish crosses of the line through the signal are the canonical
entries; histogram zero-line crossings are the same event one step earlier.

pandas-ta-classic returns the three columns named like `MACD_12_26_9`,
`MACDs_12_26_9`, `MACDh_12_26_9`; we rename to our stable schema.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_FAST = 12
_SLOW = 26
_SIGNAL = 9


class MovingAverageConvergenceDivergence(Indicator):
    name = "macd"
    table = "indicator_macd"
    pk_cols = ("symbol", "date")
    output_columns = ("macd", "macd_signal", "macd_hist")
    # EMA-based — formally needs only a few bars but the smoothing memory is
    # long. Pull ~3× the slow window so incremental values settle cleanly.
    min_history = _SLOW * 3
    pane = "oscillator"
    cadence = "eod"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        # pandas-ta-classic returns None when the input is shorter than its
        # internal warm-up requirement (instead of an all-NaN frame). Treat
        # that as "no values computable yet" and emit an empty-typed frame —
        # the writer drops all-NaN rows anyway.
        result = pd.DataFrame(index=ohlcv.index)
        raw = ta.macd(ohlcv["close"], fast=_FAST, slow=_SLOW, signal=_SIGNAL)
        if raw is None:
            result["macd"] = result["macd_signal"] = result["macd_hist"] = pd.NA
            return result
        # pandas-ta returns columns named with the parameters baked in. Rename
        # to our stable schema so the migration column names never have to
        # follow library naming choices.
        result["macd"]        = raw[f"MACD_{_FAST}_{_SLOW}_{_SIGNAL}"]
        result["macd_signal"] = raw[f"MACDs_{_FAST}_{_SLOW}_{_SIGNAL}"]
        result["macd_hist"]   = raw[f"MACDh_{_FAST}_{_SLOW}_{_SIGNAL}"]
        return result
