"""Week 19.4: psychological alignment in the confidence scorer (Layer 7)."""
from __future__ import annotations

import pytest

from nse_data.signals.confidence import BASE_SCORE, score_confidence

CHECKLIST_TABLE = [
    ("NEUTRAL_TRENDING", "long", 0.05),
    ("FOMO_EUPHORIA", "long", -0.20),
    ("BUY_RUMOR", "long", -0.10),
    ("CAPITULATION", "long", 0.15),
    ("SELL_NEWS", "short", 0.15),
    ("RELIEF_BOUNCE", "long", 0.10),
    ("DEAD_CAT_BOUNCE", "long", -0.15),
    ("FEAR_BUILDING", "long", -0.08),
]


@pytest.mark.parametrize("state,direction,delta", CHECKLIST_TABLE)
def test_psych_adjustments_match_checklist(state, direction, delta):
    base = score_confidence({}, direction=direction)
    scored = score_confidence({}, direction=direction, psych_state=state)
    assert scored - base == pytest.approx(delta)


def test_unlisted_combo_is_neutral():
    assert score_confidence({}, psych_state="SELL_NEWS", direction="long") == BASE_SCORE
    assert score_confidence({}, psych_state="FOMO_EUPHORIA", direction="short") == BASE_SCORE
    assert score_confidence({}, psych_state=None) == BASE_SCORE


def test_fomo_visibly_reduces_a_strong_long():
    """Week 19 gate: FOMO_EUPHORIA must visibly cut a long's confidence."""
    ctx = {"price_vs_vwap": "above", "vwap_slope": 0.5,
           "rsi_5m": 60.0, "trend_regime": "strong_uptrend"}
    clean = score_confidence(ctx, 4.0, "risk_on")
    fomo = score_confidence(ctx, 4.0, "risk_on", psych_state="FOMO_EUPHORIA")
    assert clean > 0.65            # would dispatch
    assert fomo == pytest.approx(clean - 0.20)
