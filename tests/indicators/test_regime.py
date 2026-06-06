"""Trend/momentum classifiers + VWAP slope (regime.py) — pure functions."""

from __future__ import annotations

import pytest

from nse_data.indicators import regime


# ---------------------------------------------------------- trend_regime

@pytest.mark.parametrize(
    "price, sma50, sma200, expected",
    [
        (110, 100, 90, "strong_uptrend"),    # price > sma50 > sma200
        (110, 90, 100, "uptrend"),           # above both, SMAs crossed
        (95, 100, 90, "sideways"),           # wedged between the averages
        (85, 100, 90, "downtrend"),          # below both, SMAs not stacked
        (85, 95, 100, "strong_downtrend"),   # price < sma50 < sma200
    ],
)
def test_classify_trend_regime(price, sma50, sma200, expected):
    assert regime.classify_trend_regime(price, sma50, sma200) == expected
    assert expected in regime.TREND_REGIMES


def test_classify_trend_regime_none_on_missing_input():
    assert regime.classify_trend_regime(None, 100, 90) is None
    assert regime.classify_trend_regime(110, None, 90) is None
    assert regime.classify_trend_regime(110, 100, None) is None


# --------------------------------------------------------- momentum_state

@pytest.mark.parametrize(
    "rsi, expected",
    [
        (85, "overbought_extreme"),
        (80, "overbought_extreme"),   # boundary lands in stronger bucket
        (75, "overbought"),
        (70, "overbought"),
        (60, "bullish"),
        (55, "bullish"),
        (50, "neutral"),
        (45, "neutral"),
        (40, "bearish"),
        (30, "bearish"),
        (25, "oversold"),
        (20, "oversold"),
        (15, "oversold_extreme"),
        (0, "oversold_extreme"),
    ],
)
def test_classify_momentum_state(rsi, expected):
    assert regime.classify_momentum_state(rsi) == expected
    assert expected in regime.MOMENTUM_STATES


def test_classify_momentum_state_none():
    assert regime.classify_momentum_state(None) is None


# ----------------------------------------------------------- price_vs_vwap

def test_price_vs_vwap():
    assert regime.price_vs_vwap(101, 100) == "above"
    assert regime.price_vs_vwap(100, 100) == "above"   # tie favours bullish read
    assert regime.price_vs_vwap(99, 100) == "below"
    assert regime.price_vs_vwap(None, 100) is None
    assert regime.price_vs_vwap(100, None) is None


# -------------------------------------------------------------- vwap_slope

def test_vwap_slope_rising():
    # 6 bars apart: 100 -> 106 over 6 bars == +1.0 per bar
    vwaps = [100, 101, 102, 103, 104, 105, 106]
    assert regime.vwap_slope(vwaps) == pytest.approx(1.0)


def test_vwap_slope_falling_uses_exactly_6_bars_ago():
    # 8 points; slope must use index -7 (value 100), not the earliest (50).
    vwaps = [50, 100, 99, 98, 97, 96, 95, 94]
    assert regime.vwap_slope(vwaps) == pytest.approx((94 - 100) / 6)


def test_vwap_slope_insufficient_history():
    assert regime.vwap_slope([100, 101, 102, 103, 104, 105]) is None   # only 6
    assert regime.vwap_slope([]) is None


def test_vwap_slope_none_endpoint():
    assert regime.vwap_slope([None, 1, 2, 3, 4, 5, 6]) is None
    assert regime.vwap_slope([1, 2, 3, 4, 5, 6, None]) is None
