"""
RSI (14, Wilder) on 5-minute bars — recomputed every minute during the session.

Same math as the EOD daily RSI; only the I/O cadence and storage shape
differ. The orchestrator routes by `cadence`: intraday source reads merged
broker-backfilled 1-min + today's live 1-min, resampled to 5-min.

`ts` is UTC epoch seconds at the 5-min bar start. The bar is "in progress"
until 5 minutes have elapsed since `ts`; values written during that window
get overwritten on the next pass via INSERT OR REPLACE.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ..base import Indicator

_LENGTH = 14


class RsiIntraday(Indicator):
    name = "rsi_5m"
    table = "indicator_rsi_5m"
    pk_cols = ("symbol", "ts")            # epoch-second key, not date string
    output_columns = (f"rsi_{_LENGTH}",)
    # Wilder smoothing has infinite memory: a fresh seed differs from the
    # continuous series by ~(1−1/14)^n, so 3× the period still carried ~3 RSI
    # points of seed error. 20× (≈3.7 sessions of 5-min bars, counted in BARS
    # by the orchestrator so overnight gaps don't shrink it) keeps the printed
    # value within ~0.1 of an infinitely-long computation — TradingView parity.
    min_history = _LENGTH * 20
    pane = "oscillator"
    cadence = "intraday"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=ohlcv.index)
        raw = ta.rsi(ohlcv["close"], length=_LENGTH)
        result[f"rsi_{_LENGTH}"] = pd.NA if raw is None else raw
        return result
