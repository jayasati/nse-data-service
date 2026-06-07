"""
Delivery conviction (FEATURE_CHECKLIST Phase 4, Week 13, task 13.5).

Delivery ratio = shares actually taken to demat / shares traded. A high ratio
means real ownership change (accumulation/distribution), not intraday churn — so
read alongside price direction it's a conviction signal. Computed nightly (18:30)
from `raw_bhavcopy_cm` into `delivery_conviction`.

    high delivery + price up    → 0.8   (accumulation)
    high delivery + price down  → 0.3   (distribution / capitulation)
    low delivery  + price up    → 0.4   (weak-hands chase)
    otherwise                   → 0.5   (indeterminate)
    + 0.1 bonus when the delivery z-score > 2 (unusually high)

"high delivery" = today's ratio is meaningfully above its 20-day norm
(z-score > 0.5), which is per-symbol relative rather than an absolute cutoff
(delivery ratios vary a lot across names).
"""

from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..scheduler import market_hours
from ..storage.db import open_db

log = structlog.get_logger()
JOB_ID = "delivery_conviction"

_WINDOW = 20            # z-score baseline
_AVG_5D = 5
_HIGH_Z = 0.5          # above this z-score counts as "high delivery"
_TREND_PCT = 0.05      # ±5% vs 5d avg → rising/falling
_Z_BONUS = 2.0


def conviction_score(high_delivery: bool, price_up: bool, z: float | None) -> float:
    """Composite delivery-conviction score in [0, 1] (task 13.5)."""
    if high_delivery and price_up:
        s = 0.8
    elif high_delivery and not price_up:
        s = 0.3
    elif not high_delivery and price_up:
        s = 0.4
    else:
        s = 0.5
    if z is not None and z > _Z_BONUS:
        s += 0.1
    return round(min(1.0, s), 3)


def _delivery_ratio(deliv_qty, volume, deliv_pct) -> float | None:
    if deliv_qty is not None and volume:
        return deliv_qty / volume
    if deliv_pct is not None:
        return deliv_pct / 100.0
    return None


def compute_symbol_delivery(conn: sqlite3.Connection, symbol: str, session_date: str) -> dict | None:
    rows = conn.execute(
        "SELECT date, close, prev_close, volume, delivery_qty, delivery_pct "
        "FROM raw_bhavcopy_cm WHERE symbol = ? AND series = 'EQ' AND close IS NOT NULL "
        "ORDER BY date DESC LIMIT ?",
        (symbol, _WINDOW + 1),
    ).fetchall()
    if not rows:
        return None
    rows = list(reversed(rows))                       # ascending
    # bar = (date, close, prev_close, volume, delivery_qty, delivery_pct)
    ratios = [r for r in (_delivery_ratio(b[4], b[3], b[5]) for b in rows) if r is not None]
    if not ratios:
        return None

    today = ratios[-1]
    avg5 = statistics.fmean(ratios[-_AVG_5D:])
    base = ratios[-_WINDOW:]
    z = None
    if len(base) >= 2:
        mean = statistics.fmean(base)
        sd = statistics.pstdev(base)
        z = round((today - mean) / sd, 3) if sd > 0 else 0.0

    if today > avg5 * (1 + _TREND_PCT):
        trend = "rising"
    elif today < avg5 * (1 - _TREND_PCT):
        trend = "falling"
    else:
        trend = "flat"

    _, close, prev_close, *_ = rows[-1]
    price_up = prev_close is not None and close > prev_close
    high = z is not None and z > _HIGH_Z
    score = conviction_score(high, price_up, z)

    return {
        "symbol": symbol, "session_date": session_date,
        "delivery_ratio": round(today, 4),
        "delivery_ratio_5d_avg": round(avg5, 4),
        "delivery_ratio_z_score": z,
        "delivery_trend": trend,
        "delivery_conviction_score": score,
    }


_COLUMNS = (
    "symbol", "session_date", "delivery_ratio", "delivery_ratio_5d_avg",
    "delivery_ratio_z_score", "delivery_trend", "delivery_conviction_score",
)


def run_delivery_pass(conn: sqlite3.Connection, symbols, *, now: datetime | None = None) -> dict:
    now = now or market_hours.now_ist()
    session_date = now.date().isoformat()
    placeholders = ",".join("?" * len(_COLUMNS))
    written = 0
    for sym in symbols:
        row = compute_symbol_delivery(conn, sym, session_date)
        if row is None:
            continue
        conn.execute(
            f"INSERT OR REPLACE INTO delivery_conviction ({','.join(_COLUMNS)}) "
            f"VALUES ({placeholders})",
            tuple(row[c] for c in _COLUMNS),
        )
        written += 1
    conn.commit()
    return {"symbols": written, "session_date": session_date}


def run_delivery_job(db_path: str) -> dict:
    from .universe import fno_plus_nifty500
    conn = open_db(db_path)
    try:
        return run_delivery_pass(conn, fno_plus_nifty500(conn))
    finally:
        conn.close()


def register_delivery_job(scheduler: BlockingScheduler, db_path: str) -> str:
    """Nightly 18:30 IST delivery-conviction compute (task 13.5). Trading-day gated."""
    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        try:
            log.info("delivery_conviction", **run_delivery_job(db_path))
        except Exception:
            log.exception("delivery_conviction_failed")

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=18, minute=30, timezone=market_hours.IST),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True,
    )
    return JOB_ID
