"""
Nightly outcome labeler (FEATURE_CHECKLIST Week 5, task 5.1).

Runs at 19:30 IST and fills `signal_outcomes` — the *label* side of the ML
dataset whose *feature* side `feature_store.py` captured at fire time. For each
recent signal it measures what actually happened after detection:

    ret_30m, ret_2h, ret_eod   intraday forward returns vs the detection price
    ret_1d, ret_3d             T+1 / T+3 daily-close returns (from bhavcopy)
    mae, mfe                   worst / best excursion during the session
    hit_t1, hit_sl             did price reach the ATR bracket intraday?

All returns are percentages (e.g. 1.5 == +1.5%) to match the rest of the
pipeline (price_change_pct, oi_change_pct).

Why a rolling re-label instead of "yesterday only": T+1d / T+3d closes don't
exist on the evening a signal fires — tomorrow's bhavcopy hasn't landed yet. So
each night we re-process the last few days and upsert; the longer-horizon
columns fill in as their bhavcopy arrives. A signal whose ret_3d is already set
is skipped (fully labeled). INSERT OR REPLACE keyed on signal_id makes the
re-run idempotent.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import cast

import pandas as pd
import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..indicators.intraday_ohlcv import read_intraday_5m
from ..scheduler import market_hours
from ..storage.db import open_db
from .paper_tracker import compute_sl_t1

log = structlog.get_logger()

JOB_ID = "signals_outcome_labeler"

# How many days back to (re)label each night, so T+1d / T+3d columns fill in
# once their bhavcopy lands. 5 covers a long weekend + a holiday comfortably.
_LOOKBACK_DAYS = 5

_OUTCOME_COLUMNS = (
    "signal_id", "symbol", "detected_at",
    "ret_30m", "ret_2h", "ret_eod", "ret_1d", "ret_3d",
    "mae", "mfe", "hit_t1", "hit_sl", "labeled_at",
)


# ============================================================================
# Pass orchestration
# ============================================================================

def run_labeler_pass(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    lookback_days: int = _LOOKBACK_DAYS,
) -> dict:
    """(Re)label recent signals. Returns {labeled, skipped} counts."""
    now = now or market_hours.now_ist()
    since_day = (now.date() - timedelta(days=lookback_days)).isoformat()

    rows = conn.execute(
        "SELECT s.id, s.symbol, s.detected_at, s.price, sf.atr_14_daily "
        "FROM signals s "
        "LEFT JOIN signal_features sf ON sf.signal_id = s.id "
        "LEFT JOIN signal_outcomes so ON so.signal_id = s.id "
        "WHERE substr(s.detected_at, 1, 10) >= ? "
        "AND (so.signal_id IS NULL OR so.ret_3d IS NULL)",   # unlabeled / incomplete
        (since_day,),
    ).fetchall()

    labeled = 0
    for signal_id, symbol, detected_at, price, atr in rows:
        outcome = label_signal(
            conn, signal_id, symbol, detected_at, price, atr, now=now,
        )
        if outcome is None:
            continue
        _upsert_outcome(conn, outcome)
        labeled += 1
    conn.commit()
    return {"candidates": len(rows), "labeled": labeled}


# ============================================================================
# Per-signal labeling
# ============================================================================

def label_signal(
    conn: sqlite3.Connection,
    signal_id: int,
    symbol: str,
    detected_at: str,
    entry_price: float | None,
    atr_14_daily: float | None,
    *,
    now: datetime,
) -> dict | None:
    """Compute every available outcome column for one signal.

    Returns a dict keyed by `_OUTCOME_COLUMNS`, or None if there's no entry
    price to measure against. Columns whose forward data isn't available yet
    are None and get filled on a later night.
    """
    if entry_price is None or entry_price <= 0:
        return None

    detected_ts = _parse_ts(detected_at)
    intraday = read_intraday_5m(conn, symbol, since_ts=detected_ts)
    after = (
        cast(pd.DataFrame, intraday.loc[intraday.index >= detected_ts])
        if not intraday.empty else intraday
    )

    ret_30m = _forward_return(after, detected_ts, 30 * 60, entry_price)
    ret_2h = _forward_return(after, detected_ts, 2 * 3600, entry_price)
    ret_eod = _eod_return(after, entry_price)
    mae, mfe = _excursions(after, entry_price)
    hit_t1, hit_sl = _bracket_hits(after, entry_price, atr_14_daily)

    daily_closes = _future_daily_closes(conn, symbol, detected_at)
    ret_1d = _pct(daily_closes[0], entry_price) if len(daily_closes) >= 1 else None
    ret_3d = _pct(daily_closes[2], entry_price) if len(daily_closes) >= 3 else None

    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "detected_at": detected_at,
        "ret_30m": ret_30m,
        "ret_2h": ret_2h,
        "ret_eod": ret_eod,
        "ret_1d": ret_1d,
        "ret_3d": ret_3d,
        "mae": mae,
        "mfe": mfe,
        "hit_t1": hit_t1,
        "hit_sl": hit_sl,
        "labeled_at": now.isoformat(),
    }


# ----------------------------------------------------------- intraday metrics

def _forward_return(
    after: pd.DataFrame, detected_ts: int, offset_secs: int, entry: float,
) -> float | None:
    """Percent return at the first bar ≥ detected_ts + offset, or None."""
    if after.empty:
        return None
    target = detected_ts + offset_secs
    ahead = cast(pd.DataFrame, after.loc[after.index >= target])
    if ahead.empty:
        return None
    return _pct(float(ahead["close"].iloc[0]), entry)


def _eod_return(after: pd.DataFrame, entry: float) -> float | None:
    """Percent return at the last intraday bar of the session, or None."""
    if after.empty:
        return None
    return _pct(float(after["close"].iloc[-1]), entry)


def _excursions(after: pd.DataFrame, entry: float) -> tuple[float | None, float | None]:
    """(MAE, MFE) as percentages over the post-entry session, or (None, None).

    MAE = most adverse (lowest low) excursion, MFE = most favorable (highest
    high), both relative to entry. For a long, MAE ≤ 0 ≤ MFE.
    """
    if after.empty:
        return (None, None)
    mae = _pct(float(after["low"].min()), entry)
    mfe = _pct(float(after["high"].max()), entry)
    return (mae, mfe)


def _bracket_hits(
    after: pd.DataFrame, entry: float, atr_14_daily: float | None,
) -> tuple[int | None, int | None]:
    """(hit_t1, hit_sl) as 1/0 — did the ATR bracket get touched intraday?

    Returns (None, None) when the bracket can't be formed (no ATR) or there are
    no post-entry bars to evaluate against.
    """
    if after.empty or atr_14_daily is None or atr_14_daily <= 0:
        return (None, None)
    sl, t1 = compute_sl_t1(entry, atr_14_daily)
    hit_t1 = 1 if float(after["high"].max()) >= t1 else 0
    hit_sl = 1 if float(after["low"].min()) <= sl else 0
    return (hit_t1, hit_sl)


# ----------------------------------------------------------- daily metrics

def _future_daily_closes(
    conn: sqlite3.Connection, symbol: str, detected_at: str,
) -> list[float]:
    """EQ daily closes strictly after the signal's date, ascending (≤3)."""
    day = detected_at[:10]
    rows = conn.execute(
        "SELECT close FROM raw_bhavcopy_cm "
        "WHERE symbol = ? AND series = 'EQ' AND date > ? AND close IS NOT NULL "
        "ORDER BY date ASC LIMIT 3",
        (symbol, day),
    ).fetchall()
    return [r[0] for r in rows]


# ----------------------------------------------------------- helpers

def _pct(value: float | None, entry: float) -> float | None:
    if value is None:
        return None
    return (value / entry - 1.0) * 100.0


def _parse_ts(detected_at: str) -> int:
    """ISO-8601 IST timestamp -> epoch seconds."""
    return int(datetime.fromisoformat(detected_at).timestamp())


def _upsert_outcome(conn: sqlite3.Connection, outcome: dict) -> None:
    placeholders = ",".join("?" * len(_OUTCOME_COLUMNS))
    conn.execute(
        f"INSERT OR REPLACE INTO signal_outcomes ({','.join(_OUTCOME_COLUMNS)}) "
        f"VALUES ({placeholders})",
        tuple(outcome[c] for c in _OUTCOME_COLUMNS),
    )


# ============================================================================
# Scheduling (nightly 19:30 IST, trading days only)
# ============================================================================

def run_labeler_job(db_path: str) -> dict:
    conn = open_db(db_path)
    try:
        return run_labeler_pass(conn)
    finally:
        conn.close()


def register_outcome_labeler(scheduler: BlockingScheduler, db_path: str) -> str:
    """Attach the 19:30-IST nightly labeler. Trading-day gated (holiday-aware)."""
    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            log.info("outcome_labeler_skipped_non_trading_day")
            return
        try:
            report = run_labeler_job(db_path)
            log.info("outcome_labeler", **report)
        except Exception:
            log.exception("outcome_labeler_failed")

    scheduler.add_job(
        _tick,
        trigger=CronTrigger(hour=19, minute=30, timezone=market_hours.IST),
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return JOB_ID
