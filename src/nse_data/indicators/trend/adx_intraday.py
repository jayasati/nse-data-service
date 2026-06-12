"""
ADX/DI (Wilder, 14) on 5-minute bars — recomputed every minute during the
session.

Same math as the EOD ADX inside the eod_full set; only the I/O cadence and
storage shape differ. The orchestrator routes by `cadence="intraday"` to read
merged broker-backfilled 1-min + today's live 1-min, resampled to 5-min.

`ts` is UTC epoch seconds at the 5-min bar start. The in-progress bar gets
overwritten on each minute pass via INSERT OR REPLACE until it closes.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_LENGTH = 14


class AdxIntraday(Indicator):
    name = "adx_5m"
    table = "indicator_adx_5m"
    pk_cols = ("symbol", "ts")            # epoch-second key, not date string
    output_columns = ("adx", "di_plus", "di_minus")
    # ADX stacks two Wilder recursions (DI smoothing, then DX smoothing), so
    # seed error decays slower than RSI's single recursion. 30× the period
    # (~5.6 sessions of 5-min bars, counted in BARS by the orchestrator so
    # overnight gaps don't shrink it) keeps the printed value within ~0.1 of
    # the continuous series — same TradingView-parity bar as rsi_5m's 20×.
    min_history = _LENGTH * 30
    pane = "oscillator"
    cadence = "intraday"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        raw = ta.adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], length=_LENGTH)
        if raw is None or raw.empty:
            result["adx"] = result["di_plus"] = result["di_minus"] = pd.NA
            return result
        result["adx"]      = raw[f"ADX_{_LENGTH}"]
        result["di_plus"]  = raw[f"DMP_{_LENGTH}"]
        result["di_minus"] = raw[f"DMN_{_LENGTH}"]
        return result
