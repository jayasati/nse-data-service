"""
Support/resistance levels.

Phase 2 (Week 9): floor pivots for the morning brief, off `raw_indices`.
Phase 4 (Week 13): a nightly per-symbol job that computes the full level set
(PDH/PDL, 52w extremes, 5d/20d ranges, nearest round number + prior failures,
pivots) from bhavcopy into `indicator_levels`, loaded into Redis at 08:45 and
shown in alerts.

Pure helpers (`floor_pivots`, `nearest_round_number`, `round_number_failures`)
are unit-tested; the readers/jobs glue them to SQLite.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..scheduler import market_hours
from ..scheduler.market_hours import IST
from ..storage.db import open_db

log = structlog.get_logger()
LEVELS_JOB_ID = "indicator_levels"


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


# ===========================================================================
# Round numbers (psychological levels)
# ===========================================================================

def _round_increment(price: float) -> float:
    """Step size for the nearest 'round' price, scaled by price magnitude."""
    if price < 100:
        return 10.0
    if price < 500:
        return 50.0
    if price < 2000:
        return 100.0
    if price < 10000:
        return 500.0
    return 1000.0


def nearest_round_number(price: float) -> float:
    inc = _round_increment(price)
    return round(price / inc) * inc


def round_number_failures(bars: list[tuple[float, float]], rn: float) -> int:
    """How many of the given (high, close) bars approached within 0.5% of `rn`
    but failed to close above it — i.e. got rejected at the round number."""
    if rn <= 0:
        return 0
    near = rn * 0.995
    return sum(1 for high, close in bars
               if high is not None and close is not None
               and high >= near and close < rn)


# ===========================================================================
# Per-symbol nightly level set (task 13.2)
# ===========================================================================

_LOOKBACK = 252        # ~1 trading year for the 52w fallback
_RANGE_5D, _RANGE_20D, _FAIL_WINDOW = 5, 20, 20


def _next_trading_day(d: date) -> date:
    nd = d + timedelta(days=1)
    for _ in range(10):
        if market_hours.is_trading_day(nd):
            return nd
        nd += timedelta(days=1)
    return nd


def _read_daily(conn: sqlite3.Connection, symbol: str, n: int) -> list[tuple]:
    """Last `n` EQ daily bars (date, high, low, close), ascending."""
    rows = conn.execute(
        "SELECT date, high, low, close FROM raw_bhavcopy_cm "
        "WHERE symbol = ? AND series = 'EQ' AND close IS NOT NULL "
        "ORDER BY date DESC LIMIT ?",
        (symbol, n),
    ).fetchall()
    return list(reversed(rows))


def _fiftytwo_week(conn: sqlite3.Connection, symbol: str, ref: date, daily: list[tuple]):
    """(high_52w, low_52w, days_since_high, days_since_low).

    Prefers raw_high_low_52w (authoritative event dates); falls back to the
    high/low over the daily window when that feed has nothing for the symbol.
    """
    hi = lo = None
    dsh = dsl = None
    try:
        for event in ("high", "low"):
            row = conn.execute(
                "SELECT new_52w_level, as_of FROM raw_high_low_52w "
                "WHERE symbol = ? AND event = ? ORDER BY as_of DESC LIMIT 1",
                (symbol, event),
            ).fetchone()
            if row and row[0] is not None:
                if event == "high":
                    hi, dsh = row[0], (ref - _ist_date(row[1])).days
                else:
                    lo, dsl = row[0], (ref - _ist_date(row[1])).days
    except sqlite3.OperationalError:
        pass
    if hi is None and daily:
        hi = max(b[1] for b in daily if b[1] is not None)
    if lo is None and daily:
        lo = min(b[2] for b in daily if b[2] is not None)
    return hi, lo, dsh, dsl


def compute_symbol_levels(conn: sqlite3.Connection, symbol: str, session_date: date) -> dict | None:
    """Full level set for one symbol, keyed to the upcoming `session_date`."""
    daily = _read_daily(conn, symbol, _LOOKBACK)
    if not daily:
        return None
    _, p_high, p_low, p_close = daily[-1]      # prior (just-closed) session
    if p_high is None or p_low is None or p_close is None:
        return None

    piv = floor_pivots(p_high, p_low, p_close)
    last5, last20 = daily[-_RANGE_5D:], daily[-_RANGE_20D:]
    hi52, lo52, dsh, dsl = _fiftytwo_week(conn, symbol, session_date, daily)

    rn = nearest_round_number(p_close)
    fails = round_number_failures([(b[1], b[3]) for b in daily[-_FAIL_WINDOW:]], rn)

    return {
        "symbol": symbol, "session_date": session_date.isoformat(),
        "high_52w": hi52, "low_52w": lo52,
        "days_since_52w_high": dsh, "days_since_52w_low": dsl,
        "pdh": p_high, "pdl": p_low,
        "range_5d_high": max(b[1] for b in last5),
        "range_5d_low": min(b[2] for b in last5),
        "range_20d_high": max(b[1] for b in last20),
        "range_20d_low": min(b[2] for b in last20),
        "nearest_round_number": rn,
        "dist_from_round_pct": round(abs(p_close - rn) / p_close * 100, 3),
        "round_number_prior_failures": fails,
        "r1": piv["r1"], "r2": piv["r2"], "s1": piv["s1"], "s2": piv["s2"],
    }


_LEVEL_COLUMNS = (
    "symbol", "session_date", "high_52w", "low_52w",
    "days_since_52w_high", "days_since_52w_low", "pdh", "pdl",
    "range_5d_high", "range_5d_low", "range_20d_high", "range_20d_low",
    "nearest_round_number", "dist_from_round_pct", "round_number_prior_failures",
    "r1", "r2", "s1", "s2",
)


def run_levels_pass(conn: sqlite3.Connection, symbols, *, now: datetime | None = None) -> dict:
    """Compute + upsert levels for `symbols`, keyed to the next trading day."""
    now = now or market_hours.now_ist()
    session_date = _next_trading_day(now.date())
    placeholders = ",".join("?" * len(_LEVEL_COLUMNS))
    written = 0
    for sym in symbols:
        row = compute_symbol_levels(conn, sym, session_date)
        if row is None:
            continue
        conn.execute(
            f"INSERT OR REPLACE INTO indicator_levels ({','.join(_LEVEL_COLUMNS)}) "
            f"VALUES ({placeholders})",
            tuple(row[c] for c in _LEVEL_COLUMNS),
        )
        written += 1
    conn.commit()
    return {"symbols": written, "session_date": session_date.isoformat()}


def run_levels_job(db_path: str) -> dict:
    from .universe import fno_plus_nifty500
    conn = open_db(db_path)
    try:
        return run_levels_pass(conn, fno_plus_nifty500(conn))
    finally:
        conn.close()


def register_levels_job(scheduler: BlockingScheduler, db_path: str) -> str:
    """Nightly 19:00 IST levels compute (task 13.2). Trading-day gated."""
    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        try:
            log.info("indicator_levels", **run_levels_job(db_path))
        except Exception:
            log.exception("indicator_levels_failed")

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=19, minute=0, timezone=IST),
        id=LEVELS_JOB_ID, max_instances=1, coalesce=True, replace_existing=True,
    )
    return LEVELS_JOB_ID
