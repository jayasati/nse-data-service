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
    market regime    risk_on          +0.10   | risk_off −0.10 | panic −0.20
    sector RS rank   1–3 (leading)    +0.08   | 9–11 (lagging) −0.08
    sector RS trend  improving        +0.03   | deteriorating −0.03
    psychology       Layer 7 (Week 19.4): see _PSYCH_ADJUSTMENTS
                     (e.g. long into FOMO_EUPHORIA −0.20, into CAPITULATION +0.15)

The result is clamped to [0, 1]. Every input is optional: a missing value
contributes 0 (neutral) rather than erroring, so a thin-data symbol still gets
a score (it just sits near the 0.50 base). The market-regime contribution
(Phase 2, task 7.5) lets the same setup score higher in a risk-on tape and get
suppressed in a panic. Phase 8 replaces this with the learned scorer trained on
`signal_features` + `signal_outcomes`.
"""

from __future__ import annotations

BASE_SCORE = 0.50


def score_confidence(
    context: dict,
    volume_ratio: float | None = None,
    regime: str | None = None,
    sector_rank: int | None = None,
    sector_trend: str | None = None,
    quality_score: float | None = None,
    bb_squeeze: bool = False,
    bearish_divergence: bool = False,
    fake_breakout: bool = False,
    credit: dict | None = None,
    is_intraday: bool = False,
    time_multiplier: float = 1.0,
    long_penalty: float = 1.0,
    earnings: dict | None = None,
    direction: str = "long",
    psych_state: str | None = None,
) -> float:
    """Confidence in [0, 1] from live context + volume + market regime + sector RS.

    `context` keys used: price_vs_vwap ('above'/'below'), vwap_slope (float),
    rsi_5m (float), trend_regime (str). `regime` is the market_state
    overall_regime. `sector_rank` (1=best..11=worst) and `sector_trend`
    ('improving'/'flat'/'deteriorating') come from the signal's sector in
    sector_state.

    The additive contributions are summed and clamped to [0,1] first, then scaled
    by two multipliers applied last (task 9.2 / 9.6):
        time_multiplier  time-of-day factor from market.time_rules (0.75–1.0)
        long_penalty     0.90 when an intermarket divergence flag is set
    All inputs optional.
    """
    score = BASE_SCORE
    # Market-directional terms: their sign assumes a LONG. For a short reaction
    # (earnings on a bad result) the same below-VWAP / downtrend / risk-off /
    # lagging-sector reads are *favourable*, so flip them.
    dir_sign = -1.0 if direction == "short" else 1.0
    score += dir_sign * _vwap_adjustment(context.get("price_vs_vwap"), context.get("vwap_slope"))
    score += dir_sign * _rsi_adjustment(context.get("rsi_5m"))
    score += dir_sign * _trend_adjustment(context.get("trend_regime"))
    score += dir_sign * _regime_adjustment(regime)
    score += dir_sign * _sector_adjustment(sector_rank, sector_trend)
    # Direction-agnostic terms (magnitude / standing quality / patterns / credit).
    score += _volume_adjustment(volume_ratio)
    score += _quality_adjustment(quality_score)
    score += _pattern_adjustment(bb_squeeze, bearish_divergence, fake_breakout)
    score += _credit_adjustment(credit, is_intraday)
    score += _earnings_adjustment(earnings, direction)
    score += _psychology_adjustment(psych_state, direction)
    score = _clamp01(score)
    return _clamp01(score * time_multiplier * long_penalty)


# Layer 7 (Week 19.4): psychological alignment. The classifier's 8 states are
# crowd-positioning reads; a long INTO euphoria/a bought rumor is fading fuel,
# a long INTO capitulation/relief is buying exhaustion. Combos not listed in
# the checklist table contribute 0 (neutral).
_PSYCH_ADJUSTMENTS = {
    ("NEUTRAL_TRENDING", "long"): 0.05,
    ("FOMO_EUPHORIA", "long"): -0.20,
    ("BUY_RUMOR", "long"): -0.10,
    ("CAPITULATION", "long"): 0.15,
    ("SELL_NEWS", "short"): 0.15,
    ("RELIEF_BOUNCE", "long"): 0.10,
    ("DEAD_CAT_BOUNCE", "long"): -0.15,
    ("FEAR_BUILDING", "long"): -0.08,
}


def _psychology_adjustment(psych_state: str | None, direction: str) -> float:
    if not psych_state:
        return 0.0
    return _PSYCH_ADJUSTMENTS.get((psych_state, direction), 0.0)


def _earnings_adjustment(earnings: dict | None, direction: str) -> float:
    """Earnings-reaction contribution (E3).

    A reaction that CONFIRMS the fundamental surprise (good result + breaking up,
    or bad result + breaking down) is trustworthy and lifts confidence; one that
    contradicts it is a likely fade and is penalised. "Buy-rumor priced in" cuts
    a long (much already in the price); "fear priced in" cuts a short. A realised
    move beyond the options-implied move is a genuine surprise and adds a little.
    """
    if not earnings:
        return 0.0
    adj = 0.0
    sign = earnings.get("surprise_sign", 0)
    if sign != 0:
        adj += 0.12 if earnings.get("confirms_direction") else -0.10
    run_up = earnings.get("run_up_class")
    if run_up == "BUY_RUMOR_IN_PLAY" and direction == "long":
        adj -= 0.10
    elif run_up == "FEAR_PRICED" and direction == "short":
        adj -= 0.10
    implied = earnings.get("implied_move_pct")
    realized = earnings.get("realized_move_pct")
    if implied and realized is not None and abs(realized) > implied:
        adj += 0.05
    return adj


def _credit_adjustment(credit: dict | None, is_intraday: bool) -> float:
    """Credit-rating contribution for a LONG signal (Week-16 credit→signal).

    Two parts:
      • EVENT — a recent rating action biases the score, within a window that is
        short for intraday (announcement day + next session ≈ 1 trading day) and
        longer for swing (≈ 5 trading days).
      • STANDING — the long-term grade quality + short-term liquidity stress,
        applied to SWING only (a standing grade shouldn't move an intraday scalp).
        Kept small because it overlaps with the fundamentals quality_score.

    A recent JUNK downgrade is hard-killed upstream (signal not sent), so it isn't
    re-handled here beyond the ordinary downgrade penalty.
    """
    if not credit:
        return 0.0
    adj = 0.0
    action = credit.get("action")
    days = credit.get("days_since")
    window = 1 if is_intraday else 5
    if action and days is not None and days <= window:
        adj += {"downgrade": -0.15, "upgrade": 0.10, "watch_negative": -0.05}.get(action, 0.0)
    if not is_intraday:                       # standing factors: swing only
        q = credit.get("quality_score")
        if q is not None:
            adj += 0.05 if q >= 85 else (-0.10 if q < 55 else 0.0)
        if credit.get("st_stressed"):
            adj -= 0.05
    return adj


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


def _regime_adjustment(regime: str | None) -> float:
    """Task 7.5: lift setups in a risk-on tape, suppress them in a panic."""
    return {
        "risk_on": 0.10,
        "neutral": 0.0,
        "risk_off": -0.10,
        "panic": -0.20,
    }.get(regime or "", 0.0)


def _pattern_adjustment(bb_squeeze: bool, bearish_divergence: bool,
                        fake_breakout: bool) -> float:
    """Task 15.3 + Phase-4 exit: pattern flags nudge confidence.

    BB squeeze (coiled, move imminent) boosts; bearish divergence and a
    fake-breakout (wick-rejected, weak volume) each cut into a long's confidence.
    """
    adj = 0.0
    if bb_squeeze:
        adj += 0.05
    if bearish_divergence:
        adj -= 0.10
    if fake_breakout:
        adj -= 0.10
    return adj


def _quality_adjustment(quality_score: float | None) -> float:
    """Task 14.5: lift high-quality names, penalise low-quality longs. A None
    score (no fundamentals) contributes 0 — absence isn't a penalty."""
    if quality_score is None:
        return 0.0
    if quality_score > 70:
        return 0.10
    if quality_score >= 50:
        return 0.05
    if quality_score >= 30:
        return 0.0
    return -0.15


def _sector_adjustment(sector_rank: int | None, sector_trend: str | None) -> float:
    """Task 8.4: favour signals in a leading, improving sector; fade lagging ones."""
    adj = 0.0
    if sector_rank is not None:
        if sector_rank <= 3:
            adj += 0.08
        elif sector_rank >= 9:
            adj -= 0.08
    if sector_trend == "improving":
        adj += 0.03
    elif sector_trend == "deteriorating":
        adj -= 0.03
    return adj


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
