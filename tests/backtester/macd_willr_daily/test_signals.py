"""Williams %R hook detection + MACD filter."""

from __future__ import annotations

import pandas as pd

from nse_data.backtester.strategies.macd_willr_daily.config import MacdWillrDailyConfig
from nse_data.backtester.strategies.macd_willr_daily.signals import (
    _macd_filter, _willr_hook, detect_long_setup,
)


def _make_df(willrs: list[float], lows: list[float],
             highs: list[float] | None = None,
             closes: list[float] | None = None,
             macds: list[float] | None = None,
             signals: list[float] | None = None,
             hists: list[float] | None = None) -> pd.DataFrame:
    n = len(willrs)
    highs = highs or [l + 1 for l in lows]
    closes = closes or [(h + l) / 2 for h, l in zip(highs, lows)]
    macds = macds or [0.1] * n
    signals = signals or [0.05] * n
    hists = hists or [m - s for m, s in zip(macds, signals)]
    return pd.DataFrame({
        "open": closes, "high": highs, "low": lows, "close": closes,
        "volume": [100] * n,
        "willr": willrs, "macd": macds, "macd_signal": signals, "macd_hist": hists,
    })


# ============================================================ willr hook

def test_willr_hook_long_fires_on_crossing_bar():
    # bar 5 was at -85; bar 6 is -75 (crossed back above -80).
    willrs = [-50.0, -60.0, -70.0, -78.0, -82.0, -85.0, -75.0]
    s = pd.Series(willrs)
    assert _willr_hook(s, t=6, level=-80.0, lookback=3, direction="LONG")


def test_willr_hook_long_rejects_when_no_prior_dip():
    # never dipped below -80
    willrs = [-60.0, -65.0, -70.0, -75.0, -78.0, -75.0, -70.0]
    s = pd.Series(willrs)
    assert not _willr_hook(s, t=6, level=-80.0, lookback=3, direction="LONG")


def test_willr_hook_long_rejects_when_still_in_oversold_at_t():
    # past was deep, current still -82 (below -80)
    willrs = [-50.0, -60.0, -70.0, -85.0, -90.0, -85.0, -82.0]
    s = pd.Series(willrs)
    assert not _willr_hook(s, t=6, level=-80.0, lookback=3, direction="LONG")


def test_willr_hook_lookback_respected():
    # dipped at index 2; lookback=3 means t-3..t=2,3,4,5 with hook at index 5.
    # Actually with t=5, lookback=3, we check k=1,2,3 → t-k = 4,3,2.
    # At index 2 willr was -85; lookback=3 catches it.
    willrs = [-50.0, -60.0, -85.0, -70.0, -65.0, -55.0]
    s = pd.Series(willrs)
    assert _willr_hook(s, t=5, level=-80.0, lookback=3, direction="LONG")

    # With lookback=2, only t-1,t-2 = indices 4,3 checked; neither <= -80.
    assert not _willr_hook(s, t=5, level=-80.0, lookback=2, direction="LONG")


def test_willr_hook_short_mirror():
    willrs = [-30.0, -25.0, -15.0, -10.0, -5.0, -25.0]   # crossed back below -20
    s = pd.Series(willrs)
    assert _willr_hook(s, t=5, level=-20.0, lookback=3, direction="SHORT")


# ============================================================ MACD filter

def test_macd_filter_long_passes_when_hist_positive_and_line_above_signal():
    df = _make_df(
        willrs=[-50.0] * 5,
        lows=[100.0] * 5,
        macds=[0.5, 0.4, 0.6, 0.7, 0.8],
        signals=[0.3, 0.3, 0.3, 0.4, 0.5],
    )
    assert _macd_filter(df, t=4, require_fresh_cross=False, direction="LONG")


def test_macd_filter_long_rejects_negative_hist():
    df = _make_df(
        willrs=[-50.0] * 3,
        lows=[100.0] * 3,
        macds=[0.3, 0.2, 0.1],
        signals=[0.5, 0.5, 0.5],   # hist = -0.4
    )
    assert not _macd_filter(df, t=2, require_fresh_cross=False, direction="LONG")


def test_macd_filter_long_fresh_cross_required():
    # Long-good filter at t but at t-1 the hist was ALREADY > 0 → not fresh.
    df = _make_df(
        willrs=[-50.0] * 3,
        lows=[100.0] * 3,
        macds=[0.6, 0.7, 0.8],
        signals=[0.5, 0.5, 0.5],   # hist all positive
    )
    assert _macd_filter(df, t=2, require_fresh_cross=False, direction="LONG")
    assert not _macd_filter(df, t=2, require_fresh_cross=True, direction="LONG")

    # Now: hist negative at t-1, positive at t → fresh cross
    df2 = _make_df(
        willrs=[-50.0] * 3,
        lows=[100.0] * 3,
        macds=[0.3, 0.4, 0.7],
        signals=[0.5, 0.5, 0.5],   # hist = -0.2, -0.1, +0.2
    )
    assert _macd_filter(df2, t=2, require_fresh_cross=True, direction="LONG")


# ============================================================ end-to-end setup

def test_detect_long_setup_fires_on_hook_plus_macd():
    cfg = MacdWillrDailyConfig(willr_length=2, swing_lookback=3, rr_target=2.0)
    # Build a series where willr hooks at bar 8 and MACD is positive.
    willrs = [-50.0, -55.0, -60.0, -70.0, -82.0, -85.0, -75.0,
              -70.0, -65.0, -60.0]
    lows = [100, 99, 98, 97, 96, 95, 95, 94, 93, 92]
    closes = [l + 0.5 for l in lows]
    df = _make_df(
        willrs=willrs, lows=lows, closes=closes,
        macds=[0.1] * 10, signals=[0.05] * 10,
    )

    setup = detect_long_setup(df, t=8, cfg=cfg)
    assert setup is not None
    assert setup.direction == "LONG"
    assert "basic" in setup.signal_tags
    # SL = lowest low in last 3 bars (indices 6,7,8 → 95,94,93) - tick
    assert setup.sl == pytest_approx(93.0 - cfg.tick)
    # target = entry + 2 * (entry - sl); entry = close[8] = 93.5
    assert setup.target == pytest_approx(93.5 + 2 * (93.5 - (93.0 - cfg.tick)))


def test_detect_long_setup_skipped_when_t_is_last_bar():
    cfg = MacdWillrDailyConfig(willr_length=2)
    df = _make_df(willrs=[-50, -85, -75], lows=[100, 99, 98])
    # t = 2 is the last bar — no next bar to fill on
    assert detect_long_setup(df, t=2, cfg=cfg) is None


def pytest_approx(v):
    import pytest
    return pytest.approx(v)
