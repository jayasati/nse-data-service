"""BB(20,2) + EMA9 golden values.

We don't pin every value to a hardcoded float — that would be brittle to
library updates. Instead we pin the structural properties that the strategy
depends on, then check a handful of values against an independent pandas
calculation. That catches drift between our wiring of pandas_ta_classic
(column names, default DDOF, etc.) and what the strategy needs to be true.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nse_data.backtester.config import BacktestConfig
from nse_data.backtester.indicators import add_bb_ema9


def _make_df(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open":   closes,
            "high":   [c + 0.5 for c in closes],
            "low":    [c - 0.5 for c in closes],
            "close":  closes,
            "volume": [100] * len(closes),
        },
        index=pd.Index(range(0, 60 * len(closes), 60), name="ts"),
    )


def test_bb_and_ema9_columns_added():
    cfg = BacktestConfig()
    df = _make_df([100.0 + i for i in range(30)])

    out = add_bb_ema9(df, cfg)

    for col in ("upper", "lower", "ema9", "x", "y"):
        assert col in out.columns


def test_early_rows_are_nan_late_rows_are_real():
    """BB(20) needs 20 closes; EMA9 needs 9. First 19 rows of upper/lower
    must be NaN, last several must be finite numbers."""
    cfg = BacktestConfig()
    df = _make_df([100.0 + i * 0.5 for i in range(40)])

    out = add_bb_ema9(df, cfg)

    assert out["upper"].iloc[:19].isna().all()
    assert out["lower"].iloc[:19].isna().all()
    assert pd.notna(out["upper"].iloc[-1])
    assert pd.notna(out["lower"].iloc[-1])
    assert pd.notna(out["ema9"].iloc[-1])


def test_bb_geometry_upper_above_close_above_lower():
    """For a randomish series the middle (mean) sits between bands. EMA9
    isn't guaranteed inside the BB, but upper > lower must hold everywhere
    a value is defined."""
    cfg = BacktestConfig()
    rng = np.random.default_rng(seed=42)
    closes = (100 + rng.normal(0, 1, size=50).cumsum()).tolist()
    df = _make_df(closes)

    out = add_bb_ema9(df, cfg)
    finite = out.dropna(subset=["upper", "lower"])

    assert (finite["upper"] > finite["lower"]).all()


def test_bb_matches_independent_rolling_std_calculation():
    """BB(20,2) upper at row i = mean(close[i-19..i]) + 2 * std(close[i-19..i], ddof=0).
    pandas_ta_classic uses population std (ddof=0) for bbands by default."""
    cfg = BacktestConfig(bb_length=20, bb_std=2.0)
    closes = [100.0 + (i % 7) * 0.3 for i in range(40)]
    df = _make_df(closes)

    out = add_bb_ema9(df, cfg)

    close_series = pd.Series(closes)
    expected_mean = close_series.rolling(20).mean()
    expected_std = close_series.rolling(20).std(ddof=0)
    expected_upper = expected_mean + 2.0 * expected_std
    expected_lower = expected_mean - 2.0 * expected_std

    # Compare the last 10 rows (where everything is defined).
    np.testing.assert_allclose(
        out["upper"].iloc[-10:].to_numpy(dtype=float),
        expected_upper.iloc[-10:].to_numpy(),
        rtol=1e-9,
        err_msg="upper BB diverges from independent rolling-std calc",
    )
    np.testing.assert_allclose(
        out["lower"].iloc[-10:].to_numpy(dtype=float),
        expected_lower.iloc[-10:].to_numpy(),
        rtol=1e-9,
        err_msg="lower BB diverges from independent rolling-std calc",
    )


def test_ema9_matches_sma_seeded_ewma():
    """pandas_ta ema seeds the EMA recursion with sma(first N) (TA-Lib style),
    not pure adjust=False from the first value. Recompute that explicitly so
    the test is independent of pandas_ta internals."""
    cfg = BacktestConfig(ema_length=9)
    closes = [100.0 + i * 0.1 for i in range(30)]
    df = _make_df(closes)

    out = add_bb_ema9(df, cfg)

    n = 9
    alpha = 2.0 / (n + 1)
    seed = float(np.mean(closes[:n]))
    expected: list[float | None] = [None] * (n - 1) + [seed]
    for c in closes[n:]:
        prev = expected[-1]
        assert prev is not None
        expected.append(alpha * c + (1 - alpha) * prev)

    np.testing.assert_allclose(
        out["ema9"].iloc[-10:].to_numpy(dtype=float),
        np.array(expected[-10:], dtype=float),
        rtol=1e-9,
    )


def test_x_and_y_are_distances():
    """x = upper - ema9 ; y = ema9 - lower."""
    cfg = BacktestConfig()
    closes = [100.0 + (i * 0.2 if i % 3 == 0 else -i * 0.1) for i in range(40)]
    df = _make_df(closes)

    out = add_bb_ema9(df, cfg).dropna()
    np.testing.assert_allclose(
        out["x"].to_numpy(dtype=float),
        (out["upper"] - out["ema9"]).to_numpy(dtype=float),
    )
    np.testing.assert_allclose(
        out["y"].to_numpy(dtype=float),
        (out["ema9"] - out["lower"]).to_numpy(dtype=float),
    )


def test_handles_insufficient_history_without_crash():
    """Only 5 closes — far below BB(20). Should return NaN columns, not raise."""
    cfg = BacktestConfig()
    df = _make_df([100.0] * 5)

    out = add_bb_ema9(df, cfg)
    assert out["upper"].isna().all()
    assert out["lower"].isna().all()
