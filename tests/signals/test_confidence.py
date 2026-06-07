"""Phase-1 confidence scorer (signals/confidence.py)."""

from __future__ import annotations

import pytest

from nse_data.signals.confidence import BASE_SCORE, score_confidence


def test_base_score_with_no_signal():
    # Empty context, no volume -> only the base.
    assert score_confidence({}, None) == pytest.approx(BASE_SCORE)


def test_all_positive_stacks():
    ctx = {"price_vs_vwap": "above", "vwap_slope": 0.5,
           "rsi_5m": 60.0, "trend_regime": "strong_uptrend"}
    # 0.50 + 0.10(vwap) + 0.10(rsi) + 0.10(trend) + 0.05(vol>3) = 0.85
    assert score_confidence(ctx, 4.0) == pytest.approx(0.85)


def test_all_negative_stacks_and_clamps_low():
    ctx = {"price_vs_vwap": "below", "vwap_slope": -0.5,
           "rsi_5m": 85.0, "trend_regime": "strong_downtrend"}
    # 0.50 - 0.10 - 0.20 - 0.20 - 0.10 = -0.10 -> clamped to 0.0
    assert score_confidence(ctx, 0.5) == pytest.approx(0.0)


def test_clamps_high_at_one():
    ctx = {"price_vs_vwap": "above", "vwap_slope": 1.0,
           "rsi_5m": 55.0, "trend_regime": "strong_uptrend"}
    # base+0.35 = 0.85, never exceeds 1.0 even with every bonus
    assert 0.0 <= score_confidence(ctx, 5.0) <= 1.0


def test_vwap_above_but_flat_slope_is_neutral():
    # 'above' alone (slope not positive) gives no bonus.
    assert score_confidence({"price_vs_vwap": "above", "vwap_slope": 0.0}, None) \
        == pytest.approx(BASE_SCORE)


@pytest.mark.parametrize("rsi,delta", [
    (60.0, 0.10),   # healthy 50–65
    (50.0, 0.10),   # inclusive lower bound
    (70.0, 0.0),    # neutral zone
    (78.0, -0.10),  # >75
    (82.0, -0.20),  # >80
])
def test_rsi_zones(rsi, delta):
    assert score_confidence({"rsi_5m": rsi}, None) == pytest.approx(BASE_SCORE + delta)


@pytest.mark.parametrize("regime,delta", [
    ("strong_uptrend", 0.10), ("uptrend", 0.05),
    ("downtrend", -0.10), ("strong_downtrend", -0.20),
    ("sideways", 0.0),
])
def test_trend_zones(regime, delta):
    assert score_confidence({"trend_regime": regime}, None) == pytest.approx(BASE_SCORE + delta)


@pytest.mark.parametrize("regime,delta", [
    ("risk_on", 0.10),
    ("neutral", 0.0),
    ("risk_off", -0.10),
    ("panic", -0.20),
    (None, 0.0),          # no regime context -> no contribution
])
def test_market_regime_contribution(regime, delta):
    # task 7.5: market regime nudges the score; passed as the 3rd arg.
    assert score_confidence({}, None, regime) == pytest.approx(BASE_SCORE + delta)


@pytest.mark.parametrize("rank,delta", [
    (1, 0.08), (3, 0.08),     # leading sector
    (5, 0.0),  (8, 0.0),      # middle
    (9, -0.08), (11, -0.08),  # lagging
    (None, 0.0),
])
def test_sector_rank_contribution(rank, delta):
    assert score_confidence({}, None, None, sector_rank=rank) == pytest.approx(BASE_SCORE + delta)


@pytest.mark.parametrize("trend,delta", [
    ("improving", 0.03), ("deteriorating", -0.03), ("flat", 0.0), (None, 0.0),
])
def test_sector_trend_contribution(trend, delta):
    assert score_confidence({}, None, None, sector_trend=trend) == pytest.approx(BASE_SCORE + delta)


def test_sector_rank_and_trend_combine():
    # leading (+0.08) AND improving (+0.03)
    assert score_confidence({}, None, None, sector_rank=1, sector_trend="improving") \
        == pytest.approx(BASE_SCORE + 0.11)
