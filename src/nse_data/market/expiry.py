"""
Expiry calendar + max-pain alignment (FEATURE_CHECKLIST Phase 2, Week 7, 7.4).

Given an IST date, says whether it's an index-options expiry and — combined with
a max-pain reading — how that should nudge a signal's confidence.

Expiry weekdays follow the checklist's stated NSE schedule:
    Nifty       weekly  -> Tuesday   (weekday 1)
    Bank Nifty  weekly  -> Thursday  (weekday 3)
    monthly             -> last Thursday of the month
(NSE has reshuffled these before; they're isolated here so a future change is a
one-line edit.)

Holiday handling: an expiry that lands on a trading holiday rolls *back* to the
previous trading day. `holiday_adjust=True` (default) applies that using the
project holiday calendar; pass False for the raw weekday rule (used in tests
that don't want to depend on the calendar).
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from ..scheduler import market_hours

# weekday() values
_TUESDAY = 1
_THURSDAY = 3

NIFTY_EXPIRY_WEEKDAY = _TUESDAY
BANKNIFTY_EXPIRY_WEEKDAY = _THURSDAY


# ============================================================================
# Holiday roll-back
# ============================================================================

def _roll_back_to_trading_day(d: date) -> date:
    """Walk back to the nearest trading day at or before `d`."""
    for _ in range(10):
        if market_hours.is_trading_day(d):
            return d
        d -= timedelta(days=1)
    return d


# ============================================================================
# Weekly expiries
# ============================================================================

def _weekly_expiry_in_week(d: date, weekday: int, holiday_adjust: bool) -> date:
    """The expiry date in `d`'s ISO week for the given weekday (holiday-adjusted)."""
    target = d + timedelta(days=weekday - d.weekday())
    return _roll_back_to_trading_day(target) if holiday_adjust else target


def is_nifty_expiry(d: date, *, holiday_adjust: bool = True) -> bool:
    return d == _weekly_expiry_in_week(d, NIFTY_EXPIRY_WEEKDAY, holiday_adjust)


def is_banknifty_expiry(d: date, *, holiday_adjust: bool = True) -> bool:
    return d == _weekly_expiry_in_week(d, BANKNIFTY_EXPIRY_WEEKDAY, holiday_adjust)


# ============================================================================
# Monthly expiry (last Thursday of the month)
# ============================================================================

def _last_thursday(year: int, month: int) -> date:
    last_dom = calendar.monthrange(year, month)[1]
    d = date(year, month, last_dom)
    d -= timedelta(days=(d.weekday() - _THURSDAY) % 7)
    return d


def is_monthly_expiry(d: date, *, holiday_adjust: bool = True) -> bool:
    last_thu = _last_thursday(d.year, d.month)
    expiry = _roll_back_to_trading_day(last_thu) if holiday_adjust else last_thu
    return d == expiry


def expiry_flags(d: date, *, holiday_adjust: bool = True) -> dict:
    """All expiry booleans for a date, in one call."""
    return {
        "is_nifty_expiry": is_nifty_expiry(d, holiday_adjust=holiday_adjust),
        "is_banknifty_expiry": is_banknifty_expiry(d, holiday_adjust=holiday_adjust),
        "is_monthly_expiry": is_monthly_expiry(d, holiday_adjust=holiday_adjust),
    }


# ============================================================================
# Max-pain alignment multiplier (task 7.4)
# ============================================================================

# How much spot must sit away from max-pain (in %) before we treat a drift as
# real, rather than noise.
_DRIFT_DEADBAND_PCT = 0.20


def max_pain_multiplier(
    signal_direction: str | None,
    max_pain: float | None,
    spot: float | None,
) -> int:
    """Confidence nudge from max-pain "pinning" pressure near expiry.

    Options open interest tends to pull spot toward the max-pain strike into
    expiry. Expected drift is therefore *toward* max-pain:
        spot above max-pain -> expected drift DOWN
        spot below max-pain -> expected drift UP

    Returns +5 if the signal's direction matches that drift, −10 if it fights
    it, and 0 when there's no data or spot is effectively at max-pain. The
    asymmetry (−10 vs +5) reflects that trading *against* expiry pinning is the
    riskier mistake.
    """
    if not signal_direction or max_pain is None or spot is None or spot <= 0:
        return 0

    drift_pct = (max_pain / spot - 1.0) * 100.0
    if abs(drift_pct) < _DRIFT_DEADBAND_PCT:
        return 0
    expected = "up" if drift_pct > 0 else "down"

    sig = _normalize_direction(signal_direction)
    if sig is None:
        return 0
    return 5 if sig == expected else -10


def _normalize_direction(direction: str) -> str | None:
    d = direction.strip().lower()
    if d in ("up", "long", "bullish", "buy", "long_buildup", "breakout_52wh"):
        return "up"
    if d in ("down", "short", "bearish", "sell", "short_buildup"):
        return "down"
    return None
