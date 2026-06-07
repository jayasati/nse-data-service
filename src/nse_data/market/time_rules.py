"""
Intraday time-of-day rules (FEATURE_CHECKLIST Phase 2, Week 9, task 9.1).

The session isn't uniform: the open is noisy, the first hour trends, lunch is
chop, and the close is a scramble. This maps an IST time to a `time_window` with
a `confidence_multiplier` (and, for two windows, a hard suppression or a raised
confidence floor). The scorer multiplies by the multiplier (task 9.2) and the
dispatcher honours suppression + the lunch floor.

    09:15–09:30  NO_TRADE           suppress all signals
    09:30–11:00  PRIME_WINDOW       ×1.00
    11:00–11:30  FIRST_EXHAUSTION   ×0.90
    11:30–13:30  LUNCH_ZONE         ×0.80, only confidence > 0.72 passes
    13:30–14:30  SECOND_WINDOW      ×1.00
    14:30–15:00  CLOSING_APPROACH   ×0.90
    15:00–15:20  CLOSING_PRESSURE   ×0.75 (scalp only)
    15:20+       NO_NEW_TRADES      suppress all signals
    (before 09:15)  PRE_OPEN        suppress
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from ..scheduler import market_hours


@dataclass(frozen=True)
class TimeRule:
    window: str
    multiplier: float
    suppressed: bool = False
    # Minimum confidence that may pass in this window (overrides the default
    # 0.65 gate); None means "use the default".
    min_confidence: float | None = None


# Boundaries as (start_inclusive, TimeRule). Evaluated in order; the first whose
# start <= t and < next start wins. Anything before 09:15 or from 15:20 on is
# suppressed.
_OPEN = time(9, 15)

_WINDOWS: list[tuple[time, TimeRule]] = [
    (time(9, 15),  TimeRule("NO_TRADE", 0.0, suppressed=True)),
    (time(9, 30),  TimeRule("PRIME_WINDOW", 1.00)),
    (time(11, 0),  TimeRule("FIRST_EXHAUSTION", 0.90)),
    (time(11, 30), TimeRule("LUNCH_ZONE", 0.80, min_confidence=0.72)),
    (time(13, 30), TimeRule("SECOND_WINDOW", 1.00)),
    (time(14, 30), TimeRule("CLOSING_APPROACH", 0.90)),
    (time(15, 0),  TimeRule("CLOSING_PRESSURE", 0.75)),
    (time(15, 20), TimeRule("NO_NEW_TRADES", 0.0, suppressed=True)),
]

_PRE_OPEN = TimeRule("PRE_OPEN", 0.0, suppressed=True)


def time_rule(now: datetime | None = None) -> TimeRule:
    """The TimeRule in force at `now` (IST). Defaults to current IST time."""
    now = now or market_hours.now_ist()
    t = now.timetz().replace(tzinfo=None)

    if t < _OPEN:
        return _PRE_OPEN

    current = _WINDOWS[0][1]
    for start, rule in _WINDOWS:
        if t >= start:
            current = rule
        else:
            break
    return current


def time_multiplier(now: datetime | None = None) -> float:
    return time_rule(now).multiplier
