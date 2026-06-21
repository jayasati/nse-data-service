"""Step 1 — daily trend / swing structure."""
from __future__ import annotations

import numpy as np
import pandas as pd

from nse_data.strategy.daily_sweep.structure import trend_at


def _ohlcv(trend_from: float, trend_to: float, n: int = 48) -> pd.DataFrame:
    """A trending series with oscillation, so swing highs AND lows both march the same way."""
    base = np.linspace(trend_from, trend_to, n)
    osc = 3.0 * np.sin(np.linspace(0, 8 * np.pi, n))   # creates the swing pivots
    close = base + osc
    idx = pd.date_range("2026-01-01", periods=n, freq="D", tz="Asia/Kolkata")
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                         "close": close, "volume": 1000}, index=idx)


def test_rising_swings_are_bullish():
    t = trend_at(_ohlcv(100, 140), k=3)
    assert t["trend"] == "bullish" and t["structure"] == 1
    assert t["swing_high"] is not None and t["swing_low"] is not None


def test_falling_swings_are_bearish():
    t = trend_at(_ohlcv(140, 100), k=3)
    assert t["trend"] == "bearish" and t["structure"] == -1


def test_too_little_history_is_none():
    short = _ohlcv(100, 110, n=5)
    assert trend_at(short, k=3)["trend"] == "none"


def test_swing_levels_are_lookahead_safe():
    # the swing at the last bar must come from CONFIRMED (past) pivots, never the last bar itself
    df = _ohlcv(100, 140)
    t = trend_at(df, k=3)
    assert t["swing_high"] <= df["high"].iloc[:-3].max()   # confirmed ≥3 bars back
