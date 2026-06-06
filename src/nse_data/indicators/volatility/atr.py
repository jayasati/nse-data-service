"""
Average True Range (14, Wilder) — latest value only.

ATR feeds `indicator_live` on two timeframes: daily (off the last ~15 bhavcopy
candles) and 5-minute (off the last ~15 intraday bars). Both call the same
helper; only the input frame differs. Unlike the time-series indicators we
don't persist the whole ATR curve — the snapshot only needs "how wide is a bar
*right now*", which sizes stops and targets.

We reuse pandas-ta-classic (the numpy-2 fork) for the same reason SMA/RSI do:
one math engine across the stack, no hand-rolled drift to reconcile in review.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

_LENGTH = 14


def atr_latest(ohlc: pd.DataFrame, length: int = _LENGTH) -> float | None:
    """Most recent ATR(`length`) value for an OHLC frame, or None.

    `ohlc` must have `high`, `low`, `close` columns in ascending-time order.
    ATR needs `length` true-range values, and the first TR needs a prior close,
    so fewer than `length + 1` rows → None (warm-up not satisfied). A trailing
    NaN (gaps in the series) also returns None rather than a half-warmed value.
    """
    if ohlc is None or len(ohlc) < length + 1:
        return None
    raw = ta.atr(ohlc["high"], ohlc["low"], ohlc["close"], length=length)
    if raw is None or raw.empty:
        return None
    value = raw.iloc[-1]
    return None if pd.isna(value) else float(value)
