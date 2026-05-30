"""In-memory Williams %R + MACD computation for the daily swing engine.

Same justification as v1's indicators module: backtester runs one-shot over
months of history per symbol; persisting indicator values just to read them
back inside the same run is wasted I/O. Pure pandas_ta_classic calls.

Williams %R returns values in [-100, 0]:
    -100 = price at lowest low of lookback window
       0 = price at highest high
"""

from __future__ import annotations

from typing import cast

import pandas as pd
import pandas_ta_classic as ta

from .config import MacdWillrDailyConfig


def add_macd_willr(df: pd.DataFrame, cfg: MacdWillrDailyConfig) -> pd.DataFrame:
    """Append willr, macd, macd_signal, macd_hist columns to `df`.

    `df` must have high/low/close columns. Early rows lacking enough history
    receive NaN — caller (signals.py) skips them.
    """
    high  = cast(pd.Series, df["high"])
    low   = cast(pd.Series, df["low"])
    close = cast(pd.Series, df["close"])

    willr = ta.willr(high, low, close, length=cfg.willr_length)
    macd  = ta.macd(close, fast=cfg.macd_fast, slow=cfg.macd_slow, signal=cfg.macd_signal)

    out = df.copy()
    out["willr"] = willr if willr is not None else pd.NA

    if macd is None or macd.empty:
        out["macd"] = pd.NA
        out["macd_signal"] = pd.NA
        out["macd_hist"]   = pd.NA
        return out

    suffix = f"_{cfg.macd_fast}_{cfg.macd_slow}_{cfg.macd_signal}"
    out["macd"]        = macd[f"MACD{suffix}"]
    out["macd_signal"] = macd[f"MACDs{suffix}"]
    out["macd_hist"]   = macd[f"MACDh{suffix}"]
    return out
