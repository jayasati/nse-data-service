"""
Support/resistance levels (Phase 2, Week 9 — used by the morning brief).

Classic floor pivots from a session's high/low/close, plus recent swing
high/low. Indices live in `raw_indices` (one row per 5-min capture; the last row
of a session carries that session's full high/low/last), so the readers pick the
prior *completed* session relative to a reference date.

Pure `floor_pivots` is unit-tested; the readers glue it to `raw_indices`.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from ..scheduler.market_hours import IST


def floor_pivots(high: float, low: float, close: float) -> dict[str, float]:
    """Classic floor-trader pivots from one session's H/L/C."""
    p = (high + low + close) / 3.0
    rng = high - low
    return {
        "pivot": round(p, 2),
        "r1": round(2 * p - low, 2),
        "s1": round(2 * p - high, 2),
        "r2": round(p + rng, 2),
        "s2": round(p - rng, 2),
        "r3": round(high + 2 * (p - low), 2),
        "s3": round(low - 2 * (high - p), 2),
    }


def _ist_date(epoch: int) -> date:
    return datetime.fromtimestamp(epoch, tz=IST).date()


def prior_session_ohlc(
    conn: sqlite3.Connection, index_symbol: str, ref_date: date,
) -> tuple[float, float, float] | None:
    """(high, low, last) of the latest session strictly before `ref_date`.

    Scans recent `raw_indices` rows newest-first and returns the first one whose
    IST date < ref_date — i.e. the last completed session. None if unavailable
    or the row lacks H/L/last.
    """
    rows = conn.execute(
        "SELECT as_of, high, low, last FROM raw_indices "
        "WHERE index_symbol = ? ORDER BY as_of DESC LIMIT 2000",
        (index_symbol,),
    ).fetchall()
    for as_of, high, low, last in rows:
        if _ist_date(as_of) < ref_date:
            if high is None or low is None or last is None:
                return None
            return (high, low, last)
    return None


def index_pivots(
    conn: sqlite3.Connection, index_symbol: str, ref_date: date,
) -> dict[str, float] | None:
    """Floor pivots for an index off its last completed session before ref_date."""
    ohlc = prior_session_ohlc(conn, index_symbol, ref_date)
    if ohlc is None:
        return None
    return floor_pivots(*ohlc)


def swing_levels(
    conn: sqlite3.Connection, index_symbol: str, *, lookback_rows: int = 1500,
) -> tuple[float | None, float | None]:
    """(recent_high, recent_low) over the last `lookback_rows` captures —
    an approximate swing band (~20 sessions of 5-min rows)."""
    row = conn.execute(
        "SELECT MAX(high), MIN(low) FROM (SELECT high, low FROM raw_indices "
        "WHERE index_symbol = ? ORDER BY as_of DESC LIMIT ?)",
        (index_symbol, lookback_rows),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)
