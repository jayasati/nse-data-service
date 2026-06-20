"""Announcement-reaction tracker — sell-the-news / better-than-feared (FEATURE_CHECKLIST
Week 20, tasks 20.1/20.2/20.3).

When a high-priority announcement lands, the first few minutes tell you whether the market
believes it. We classify the reaction from the initial jump and where price sits 5 minutes
later, then derive two contrarian signals:

    SPIKE_AND_HOLD   jump >2% and still >1.5% after 5m   → genuine reaction
    SPIKE_AND_FADE   jump >2% but back under 0.8%        → the pop is being sold
    NO_REACTION      moved <1% either way                → fully priced in
    REVERSE_REACTION positive news but price fell >1%    → sell-the-news confirmed

    sell_the_news_confirmed     SPIKE_AND_FADE or REVERSE on a POSITIVE announcement (20.2)
    better_than_feared_reversal a FEARED name (negative pre-event run) that RISES post-news (20.3)

Pure `classify_reaction` + the two predicates are unit-tested; `track_reaction` reads intraday
bars. Feeds the gated dispatch path like every signal.
"""
from __future__ import annotations

import sqlite3

import structlog

log = structlog.get_logger()

SPIKE_AND_HOLD = "SPIKE_AND_HOLD"
SPIKE_AND_FADE = "SPIKE_AND_FADE"
NO_REACTION = "NO_REACTION"
REVERSE_REACTION = "REVERSE_REACTION"

_SPIKE_MIN = 2.0        # initial jump that counts as a "spike"
_HOLD_MIN = 1.5         # still above this after 5m → held
_FADE_MAX = 0.8         # back under this after 5m → faded
_FLAT = 1.0             # |move| under this either way → no reaction
_REVERSE = -1.0         # positive news but price under this after 5m → reversed


def classify_reaction(jump_pct: float | None, level_5m_pct: float | None, *,
                      positive_news: bool = True) -> str | None:
    """Reaction type from the initial jump and the +5m level (both % vs pre-announcement).
    None when inputs are missing or the pattern is ambiguous."""
    if jump_pct is None or level_5m_pct is None:
        return None
    if jump_pct > _SPIKE_MIN:
        if level_5m_pct > _HOLD_MIN:
            return SPIKE_AND_HOLD
        if level_5m_pct < _FADE_MAX:
            return SPIKE_AND_FADE
    if positive_news and level_5m_pct < _REVERSE:
        return REVERSE_REACTION
    if abs(jump_pct) < _FLAT and abs(level_5m_pct) < _FLAT:
        return NO_REACTION
    return None


def is_sell_the_news(reaction: str | None, *, positive_news: bool = True) -> bool:
    """Task 20.2 — the pop on good news is being sold."""
    return positive_news and reaction in (SPIKE_AND_FADE, REVERSE_REACTION)


def is_better_than_feared(pre_event_run_pct: float | None, post_news_pct: float | None) -> bool:
    """Task 20.3 — a feared name (negative run into the event) rises after the news lands."""
    if pre_event_run_pct is None or post_news_pct is None:
        return False
    return pre_event_run_pct < -3.0 and post_news_pct > 1.0


def track_reaction(conn: sqlite3.Connection, symbol: str, announce_ts: int) -> dict | None:
    """Compute (jump, +5m level) for an announcement at `announce_ts` from 5-min bars and
    classify. jump = first bar at/after the announcement vs the bar before it; level_5m =
    the bar ~5 min later vs the same base. None when there aren't enough bars yet."""
    from ..indicators.intraday_ohlcv import read_intraday_5m

    bars = read_intraday_5m(conn, symbol, since_ts=announce_ts - 600)
    if bars is None or bars.empty or len(bars) < 2:
        return None
    pre = bars[bars.index < announce_ts]
    post = bars[bars.index >= announce_ts]
    if pre.empty or post.empty:
        return None
    base = float(pre["close"].iloc[-1])
    if base <= 0:
        return None
    jump = (float(post["close"].iloc[0]) - base) / base * 100.0
    after5 = post[post.index >= announce_ts + 300]
    level5 = ((float(after5["close"].iloc[0]) - base) / base * 100.0
              if not after5.empty else jump)
    reaction = classify_reaction(round(jump, 2), round(level5, 2))
    return {"symbol": symbol, "jump_pct": round(jump, 2),
            "level_5m_pct": round(level5, 2), "reaction": reaction}
