"""
Regime + momentum classification — the categorical tags written to
`indicator_live` every minute.

These are pure functions: price/indicator numbers in, a label string (or None
when an input is missing) out. No DB, no I/O — the live-snapshot job supplies
the values and persists the result. Keeping the thresholds here, in one place,
means the signal engine and the dashboard read identical definitions.

Tag vocabularies (FEATURE_CHECKLIST Week 3, tasks 3.3–3.5):

  trend_regime    strong_uptrend / uptrend / sideways / downtrend / strong_downtrend
  momentum_state  overbought_extreme / overbought / bullish / neutral /
                  bearish / oversold / oversold_extreme
  price_vs_vwap   above / below
"""

from __future__ import annotations

from typing import Sequence

# Exposed so callers/tests can assert a value is in-vocabulary without
# re-typing the strings. Ordered most-bullish → most-bearish.
TREND_REGIMES: tuple[str, ...] = (
    "strong_uptrend", "uptrend", "sideways", "downtrend", "strong_downtrend",
)
MOMENTUM_STATES: tuple[str, ...] = (
    "overbought_extreme", "overbought", "bullish", "neutral",
    "bearish", "oversold", "oversold_extreme",
)


def classify_trend_regime(
    price: float | None,
    sma50: float | None,
    sma200: float | None,
) -> str | None:
    """Classify trend from price vs the 50/200-day SMAs (task 3.4).

    The two "strong" buckets require a fully-stacked alignment
    (price > sma50 > sma200, or the mirror image) — the textbook signature of
    a trend with the fast average leading the slow one. The plain up/down
    buckets only need price on one side of *both* averages, with the SMAs in
    any order. Anything else — price wedged between the two averages — is
    sideways.

    Returns None if any input is missing (e.g. <200 daily bars, so sma200 is
    still NULL); the caller stores NULL and the signal engine skips the symbol.
    """
    if price is None or sma50 is None or sma200 is None:
        return None
    if price > sma50 > sma200:
        return "strong_uptrend"
    if price < sma50 < sma200:
        return "strong_downtrend"
    if price > sma50 and price > sma200:
        return "uptrend"
    if price < sma50 and price < sma200:
        return "downtrend"
    return "sideways"


# RSI bucket lower bounds, highest first. Each bound is inclusive: a value at
# the boundary lands in the *stronger* bucket (e.g. exactly 70 → overbought).
_MOMENTUM_BUCKETS: tuple[tuple[float, str], ...] = (
    (80.0, "overbought_extreme"),
    (70.0, "overbought"),
    (55.0, "bullish"),
    (45.0, "neutral"),
    (30.0, "bearish"),
    (20.0, "oversold"),
    # below 20 → oversold_extreme (the fall-through)
)


def classify_momentum_state(rsi: float | None) -> str | None:
    """Bucket RSI(5m) into a momentum label (task 3.5).

    Returns None if RSI is missing (warm-up not yet satisfied).
    """
    if rsi is None:
        return None
    for lower, label in _MOMENTUM_BUCKETS:
        if rsi >= lower:
            return label
    return "oversold_extreme"


def price_vs_vwap(price: float | None, vwap: float | None) -> str | None:
    """'above' / 'below' depending on where price sits relative to VWAP.

    Price exactly on the VWAP is reported 'above' (a tie favours the bullish
    read; the slope, not this flag, is what the signal engine leans on).
    Returns None if either input is missing.
    """
    if price is None or vwap is None:
        return None
    return "above" if price >= vwap else "below"


def vwap_slope(vwaps: Sequence[float | None]) -> float | None:
    """Slope of the VWAP anchor over the last 6 five-minute bars (task 3.3).

        slope = (vwap_now - vwap_6_bars_ago) / 6

    Positive = rising anchor = bullish context; negative = falling = bearish.

    `vwaps` is the session's VWAP series in ascending-time order. We read the
    last value and the one 6 bars (30 minutes) earlier. Fewer than 7 points
    (less than 30 minutes into the session) → None. A NULL at either endpoint
    also yields None rather than a bogus number.
    """
    if vwaps is None or len(vwaps) < 7:
        return None
    current = vwaps[-1]
    earlier = vwaps[-7]
    if current is None or earlier is None:
        return None
    return (current - earlier) / 6.0
