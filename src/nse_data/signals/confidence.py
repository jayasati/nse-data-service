"""
Phase-1 confidence scorer (FEATURE_CHECKLIST Week 5, task 5.5).

A deliberately simple, transparent rule-stack — not a model. It takes the live
indicator context (the same dict `enrich.read_live_context` returns) plus the
signal's volume ratio and produces a single 0–1 number the dispatcher gates on
(task 5.6: send only if confidence > 0.65).

    base                         0.50
    VWAP alignment   above & slope>0  +0.10   | below  −0.10
    RSI(5m) zone     50–65            +0.10   | >75 −0.10 | >80 −0.20
    trend regime     strong_uptrend   +0.10   | uptrend +0.05
                     downtrend        −0.10   | strong_downtrend −0.20
    volume ratio     >3×              +0.05   | <1×  −0.10

The result is clamped to [0, 1]. Every input is optional: a missing value
contributes 0 (neutral) rather than erroring, so a thin-data symbol still gets
a score (it just sits near the 0.50 base). Phase 8 replaces this with the
learned scorer trained on `signal_features` + `signal_outcomes`.
"""

from __future__ import annotations

BASE_SCORE = 0.50


def score_confidence(context: dict, volume_ratio: float | None = None) -> float:
    """Confidence in [0, 1] from the live context + volume ratio.

    `context` keys used: price_vs_vwap ('above'/'below'), vwap_slope (float),
    rsi_5m (float), trend_regime (str). All optional.
    """
    score = BASE_SCORE
    score += _vwap_adjustment(context.get("price_vs_vwap"), context.get("vwap_slope"))
    score += _rsi_adjustment(context.get("rsi_5m"))
    score += _trend_adjustment(context.get("trend_regime"))
    score += _volume_adjustment(volume_ratio)
    return _clamp01(score)


def _vwap_adjustment(price_vs_vwap: str | None, vwap_slope: float | None) -> float:
    """Reward a rising anchor with price above it; penalise price below VWAP."""
    if price_vs_vwap == "above" and vwap_slope is not None and vwap_slope > 0:
        return 0.10
    if price_vs_vwap == "below":
        return -0.10
    return 0.0


def _rsi_adjustment(rsi: float | None) -> float:
    """Healthy 50–65 momentum is good; overbought is a fade risk."""
    if rsi is None:
        return 0.0
    if rsi > 80:
        return -0.20
    if rsi > 75:
        return -0.10
    if 50 <= rsi <= 65:
        return 0.10
    return 0.0


def _trend_adjustment(trend_regime: str | None) -> float:
    return {
        "strong_uptrend": 0.10,
        "uptrend": 0.05,
        "downtrend": -0.10,
        "strong_downtrend": -0.20,
    }.get(trend_regime or "", 0.0)


def _volume_adjustment(volume_ratio: float | None) -> float:
    if volume_ratio is None:
        return 0.0
    if volume_ratio > 3:
        return 0.05
    if volume_ratio < 1:
        return -0.10
    return 0.0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
