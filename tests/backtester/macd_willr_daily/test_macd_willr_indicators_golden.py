"""Williams %R + MACD golden values.

We verify our wiring of pandas_ta_classic (column names, defaults) against
independent hand calculations on small fixed series. Catches drift if the
library renames its outputs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nse_data.backtester.strategies.macd_willr_daily.config import MacdWillrDailyConfig
from nse_data.backtester.strategies.macd_willr_daily.indicators import add_macd_willr


def _make_df(highs: list[float], lows: list[float], closes: list[float]) -> pd.DataFrame:
    assert len(highs) == len(lows) == len(closes)
    return pd.DataFrame(
        {
            "open":   closes,
            "high":   highs,
            "low":    lows,
            "close":  closes,
            "volume": [100] * len(closes),
        },
        index=pd.Index([f"2026-01-{i+1:02d}" for i in range(len(closes))], name="date"),
    )


def test_indicator_columns_added():
    cfg = MacdWillrDailyConfig()
    closes = [100.0 + i for i in range(80)]
    df = _make_df(
        highs=[c + 1 for c in closes],
        lows=[c - 1 for c in closes],
        closes=closes,
    )

    out = add_macd_willr(df, cfg)

    for col in ("willr", "macd", "macd_signal", "macd_hist"):
        assert col in out.columns


def test_willr_in_minus_100_to_0_range():
    cfg = MacdWillrDailyConfig(willr_length=14)
    rng = np.random.default_rng(42)
    closes = (100 + rng.normal(0, 1, 60).cumsum()).tolist()
    df = _make_df(
        highs=[c + 0.5 for c in closes],
        lows=[c - 0.5 for c in closes],
        closes=closes,
    )

    out = add_macd_willr(df, cfg)
    valid = out["willr"].dropna()

    assert (valid >= -100).all()
    assert (valid <= 0).all()


def test_willr_matches_hand_calc_at_known_bar():
    """willr = -100 × (HH - close) / (HH - LL). Construct so the last bar
    sits at the high; willr should be 0."""
    cfg = MacdWillrDailyConfig(willr_length=14)
    closes = [float(c) for c in range(50, 64)]    # 50..63, 14 bars
    df = _make_df(
        highs=[c + 0.0 for c in closes],          # high = close
        lows=[c - 0.5 for c in closes],
        closes=closes,
    )

    out = add_macd_willr(df, cfg)
    last = out["willr"].iloc[-1]
    # HH = 63.0; close = 63.0; willr = -100 × 0 / (63 - 49.5) = 0
    assert abs(last) < 1e-9


def test_macd_signal_lags_macd_line_on_acceleration():
    """On an accelerating series (quadratic), MACD keeps rising and the
    signal (EMA of MACD) lags strictly below it. A purely linear series
    converges to MACD == signal so wouldn't show lag."""
    cfg = MacdWillrDailyConfig()
    closes = [100.0 + i * i * 0.01 for i in range(100)]    # quadratic
    df = _make_df(
        highs=[c + 0.5 for c in closes],
        lows=[c - 0.5 for c in closes],
        closes=closes,
    )

    out = add_macd_willr(df, cfg).dropna()

    # Last 20 bars: MACD strictly above signal
    assert (out["macd"].iloc[-20:] > out["macd_signal"].iloc[-20:]).all()


def test_macd_hist_equals_line_minus_signal():
    cfg = MacdWillrDailyConfig()
    rng = np.random.default_rng(7)
    closes = (100 + rng.normal(0, 1, 100).cumsum()).tolist()
    df = _make_df(
        highs=[c + 0.5 for c in closes],
        lows=[c - 0.5 for c in closes],
        closes=closes,
    )

    out = add_macd_willr(df, cfg).dropna()
    np.testing.assert_allclose(
        out["macd_hist"].to_numpy(dtype=float),
        (out["macd"] - out["macd_signal"]).to_numpy(dtype=float),
        atol=1e-9,
    )


def test_insufficient_history_returns_nans_not_error():
    cfg = MacdWillrDailyConfig()
    df = _make_df([101], [99], [100])     # 1 bar — far below any minimum

    out = add_macd_willr(df, cfg)
    assert out["willr"].isna().all()
    assert out["macd"].isna().all()
