"""ATR(14) latest-value helper (volatility/atr.py)."""

from __future__ import annotations

import pandas as pd

from nse_data.indicators.volatility.atr import atr_latest


def _frame(highs, lows, closes) -> pd.DataFrame:
    return pd.DataFrame({"high": highs, "low": lows, "close": closes})


def test_atr_value_is_true_range_when_steady():
    n = 20
    df = _frame([101.0] * n, [99.0] * n, [100.0] * n)
    val = atr_latest(df, length=14)
    # true range each bar = max(high-low=2, |high-prevclose|=1, |low-prevclose|=1)
    # = 2.0, so Wilder ATR settles at 2.0.
    assert val == 2.0


def test_atr_none_when_insufficient_history():
    # length + 1 = 15 bars needed; give 10.
    n = 10
    assert atr_latest(_frame([101.0] * n, [99.0] * n, [100.0] * n), length=14) is None


def test_atr_none_on_empty():
    assert atr_latest(pd.DataFrame(columns=["high", "low", "close"]), 14) is None
    assert atr_latest(None, 14) is None


def test_atr_returns_float():
    n = 30
    highs = [100 + i + 1 for i in range(n)]
    lows = [100 + i - 1 for i in range(n)]
    closes = [100 + i for i in range(n)]
    val = atr_latest(_frame(highs, lows, closes), length=14)
    assert isinstance(val, float) and val > 0
