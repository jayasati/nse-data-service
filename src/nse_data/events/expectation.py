"""Expectation-proxy model for the earnings engine (Phase 5, E2).

Pure functions (no DB) that turn raw pre-event measurements into the classes and
the composite "expectation proxy" the engine reasons with. The proxy stands in
for the consensus estimates this project doesn't have: what the market has
*already priced in* (run-up + options positioning) plus the stock's recent
growth trend. The post-result trigger (E3) compares the actual reaction against
this to judge surprise.

Sign convention for ``expectation_proxy_score``: +1 = market positioned for a
RISE (bullish lean already priced), -1 = positioned for a FALL. A reaction that
*contradicts* a strong lean is the high-conviction surprise.
"""
from __future__ import annotations

import math

# Pre-result run-up bands (5-day % move into the event). Mirrors the
# FEATURE_CHECKLIST 18.2 buy-rumor / fear-priced taxonomy.
RUN_UP_BANDS = (
    (8.0, "BUY_RUMOR_IN_PLAY"),     # >= +8%   : a lot already priced in (fade risk on a mere in-line)
    (3.0, "MILD_ANTICIPATION"),     # +3..+8%
    (-3.0, "NORMAL"),               # -3..+3%
    (-8.0, "MILD_FEAR"),            # -8..-3%
)
RUN_UP_FLOOR_CLASS = "FEAR_PRICED"  # <= -8% : market already bracing for bad news


def classify_runup(run_up_5d: float | None) -> str:
    """Bucket the 5-day pre-event run-up into a priced-in class."""
    if run_up_5d is None:
        return "UNKNOWN"
    for threshold, label in RUN_UP_BANDS:
        if run_up_5d >= threshold:
            return label
    return RUN_UP_FLOOR_CLASS


# How each run-up class biases the "already priced" lean, in [-1, +1].
_RUNUP_LEAN = {
    "BUY_RUMOR_IN_PLAY": 0.8,
    "MILD_ANTICIPATION": 0.4,
    "NORMAL": 0.0,
    "MILD_FEAR": -0.4,
    "FEAR_PRICED": -0.8,
    "UNKNOWN": 0.0,
}


def classify_oi_buildup(
    oi_change_pct: float | None, price_change_pct: float | None,
) -> str:
    """Futures positioning from OI vs price (classic four-quadrant, simplified).

    price up + OI up  -> LONG_BUILDUP ; price down + OI up -> SHORT_BUILDUP ;
    otherwise NEUTRAL (covering / unwinding / no clear signal).
    """
    if oi_change_pct is None or price_change_pct is None or abs(oi_change_pct) < 1.0:
        return "NEUTRAL"
    if price_change_pct > 0:
        return "LONG_BUILDUP"
    if price_change_pct < 0:
        return "SHORT_BUILDUP"
    return "NEUTRAL"


def implied_move_from_iv(iv_pct: float | None, days_to_event: float) -> float | None:
    """Expected move into the event from ATM IV: IV% * sqrt(days/365).

    A 40% IV with 5 days to the result implies ~4.7% expected move. Returns None
    when IV is missing or non-positive.
    """
    if iv_pct is None or iv_pct <= 0 or days_to_event <= 0:
        return None
    return round(iv_pct * math.sqrt(days_to_event / 365.0), 2)


def classify_fundamental_trend(growth: dict | None) -> str:
    """Recent growth trajectory from the last reported quarter's YoY figures.

    Pre-event we don't have the upcoming actuals, so this is the *baseline*
    expectation: is the company growing, flat, or declining lately.
    """
    if not growth:
        return "UNKNOWN"
    rev = growth.get("yoy_revenue_pct")
    pat = growth.get("yoy_pat_pct")
    ref = pat if pat is not None else rev
    if ref is None:
        return "UNKNOWN"
    if ref >= 20:
        return "STRONG_GROWTH"
    if ref >= 5:
        return "GROWTH"
    if ref >= -5:
        return "FLAT"
    return "DECLINE"


_FUNDAMENTAL_LEAN = {
    "STRONG_GROWTH": 0.5, "GROWTH": 0.25, "FLAT": 0.0,
    "DECLINE": -0.4, "UNKNOWN": 0.0,
}


def expectation_proxy_score(
    run_up_class: str, fundamental_class: str, pcr: float | None,
) -> float:
    """Composite priced-in lean in [-1, +1] (no consensus estimates needed).

    Blends the run-up lean (how much is already priced), the recent growth
    trajectory, and the options PCR (PCR > ~1.2 = put-heavy/bearish lean,
    < ~0.8 = call-heavy/bullish). Weighted toward the run-up — it's the most
    direct read of what's already in the price.
    """
    score = 0.6 * _RUNUP_LEAN.get(run_up_class, 0.0)
    score += 0.3 * _FUNDAMENTAL_LEAN.get(fundamental_class, 0.0)
    if pcr is not None:
        if pcr >= 1.2:
            score -= 0.1
        elif pcr <= 0.8:
            score += 0.1
    return round(max(-1.0, min(1.0, score)), 3)


def is_notable(setup: dict) -> bool:
    """Whether a pre-event setup is worth a Telegram flag.

    Flag the cases a trader should be cautious about: a meaningful move already
    priced in (buy/sell-rumor), a large expected move, or a clearly skewed lean.
    """
    if setup.get("run_up_class") in ("BUY_RUMOR_IN_PLAY", "FEAR_PRICED"):
        return True
    if (setup.get("implied_move_pct") or 0) >= 6.0:
        return True
    if abs(setup.get("expectation_proxy_score") or 0.0) >= 0.5:
        return True
    return False


def build_flag_message(symbol: str, event_date: str, setup: dict) -> str:
    """Human-readable pre-event flag: what's priced in, why it matters, caution."""
    lean = setup.get("expectation_proxy_score") or 0.0
    direction = "RISE" if lean > 0.15 else "FALL" if lean < -0.15 else "NEUTRAL"
    lines = [
        f"\U0001F4C5 PRE-EARNINGS FLAG — {symbol}  (result due {event_date})",
    ]
    ru5 = setup.get("run_up_5d")
    if ru5 is not None:
        lines.append(f"Run-up 5d: {ru5:+.1f}%  [{setup.get('run_up_class')}]")
    im = setup.get("implied_move_pct")
    if im is not None:
        lines.append(f"Options imply ±{im:.1f}% move (ATM IV {setup.get('iv_atm')})")
    if setup.get("pcr") is not None:
        lines.append(f"PCR {setup['pcr']:.2f} · OI {setup.get('oi_buildup_class')}")
    g_rev, g_pat = setup.get("growth_yoy_rev"), setup.get("growth_yoy_pat")
    if g_rev is not None or g_pat is not None:
        lines.append(
            f"Recent trend: rev YoY {g_rev:+.0f}% / PAT YoY {g_pat:+.0f}% "
            f"[{setup.get('fundamental_class')}]".replace("+nan", "?")
        )
    if setup.get("sector_rank") is not None:
        lines.append(f"Sector RS rank {setup['sector_rank']} ({setup.get('sector_trend')})")
    if setup.get("consensus_rev_est") is not None or setup.get("consensus_eps_est") is not None:
        lines.append(
            f"Consensus: rev ~₹{setup.get('consensus_rev_est')}cr / "
            f"EPS ~{setup.get('consensus_eps_est')} (beat/miss decided on the result)"
        )
    # the punchline
    caution = {
        "BUY_RUMOR_IN_PLAY": "Much may be priced in — an in-line result can still sell off.",
        "FEAR_PRICED": "Bad news may be priced in — a less-bad result can pop.",
    }.get(setup.get("run_up_class"), "")
    lines.append(f"Lean: priced for {direction} (proxy {lean:+.2f}). {caution}".rstrip())
    lines.append("Not a trade — wait for the post-result reaction.")
    return "\n".join(lines)
