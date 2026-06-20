"""Tests for the formal 9-layer confidence engine (W25)."""
from __future__ import annotations

from nse_data.signals import confidence_engine as ce


def test_layer_bounds_and_values():
    assert ce.layer1_regime("risk_on") == 0.12
    assert ce.layer1_regime("panic") == -0.15
    assert ce.layer1_regime("risk_on", divergence=True) == 0.07          # −0.05 divergence
    assert ce.layer2_sector(1, 22, "improving") == 0.10                  # +0.08 +0.03 → clipped to +0.10
    assert ce.layer2_sector(21, 22, None) == -0.08
    assert ce.layer3_fundamental(80) == 0.10 and ce.layer3_fundamental(20) == -0.15
    assert ce.layer3_fundamental(None) == 0.0
    # technical: above+slope (0.10) + rsi 60 (0.05) + uptrend (0.05) = 0.20 → clipped 0.15
    assert ce.layer4_technical("above", 0.2, 60, "uptrend") == 0.15
    assert ce.layer8_institutional(0.75) == 0.10 and ce.layer8_institutional(0.35) == -0.08


def test_layer5_signal_quality_capped():
    strong_buildup = ce.layer5_signal_quality(
        "long_buildup", {"oi_change_pct": 12, "price_change_pct": 3, "volume_ratio": 4})
    assert strong_buildup == 0.10                          # 0.08+0.03+0.05 capped at 0.10
    assert ce.layer5_signal_quality("credit_downgrade_junk", {}) == 0.10
    assert ce.layer5_signal_quality("result_beat", {"earnings_quality": "HIGH"}) == 0.08


def test_layer6_confluence_and_layer7_psych():
    assert ce.layer6_confluence(2) == 0.06 and ce.layer6_confluence(3) == 0.10
    assert ce.layer7_psych("FOMO_EUPHORIA", "long") == -0.20
    assert ce.layer7_psych("CAPITULATION", "long") == 0.15
    assert ce.layer7_psych("SELL_NEWS", "short") == 0.15
    assert ce.layer7_psych("NEUTRAL_TRENDING", "short") == 0.0          # table is direction-keyed


def test_layer9_event_proximity():
    assert ce.layer9_event(12, 3, None, False, "long") == -0.12         # exhausted run into event
    assert ce.layer9_event(None, None, 2.5, False, "long") == -0.08     # IV blow-off
    assert ce.layer9_event(None, None, None, True, "short") == 0.08     # spike-and-fade short (clip)


def test_compute_confidence_strong_vs_weak():
    strong = ce.compute_confidence(
        {"regime": "risk_on", "sector_rank": 1, "sector_n": 22, "quality_score": 80,
         "price_vs_vwap": "above", "vwap_slope": 0.2, "rsi_5m": 60, "trend_regime": "uptrend",
         "oi_change_pct": 12, "price_change_pct": 3, "volume_ratio": 4, "confluent_signals": 2,
         "psych_state": "NEUTRAL_TRENDING", "smart_money": 0.75},
        signal_type="long_buildup", direction="long")
    assert strong["final"] >= 0.95 and len(strong["layers"]) == 9

    weak = ce.compute_confidence(
        {"regime": "panic", "sector_rank": 21, "sector_n": 22, "quality_score": 20,
         "price_vs_vwap": "below", "rsi_5m": 80, "psych_state": "FOMO_EUPHORIA",
         "smart_money": 0.35, "pre_event_run_10d": 12, "days_to_event": 3},
        signal_type="long_buildup", direction="long")
    assert weak["final"] <= 0.15                          # stacked penalties floor it
