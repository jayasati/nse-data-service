"""Short-squeeze detector (FEATURE_CHECKLIST Week 24, task 24.2).

The most explosive long setup: a heavily-shorted name that's been driven down, where the
shorts start covering and real buyers (rising delivery) step in as price turns up. The full
condition wants SLB (securities-lending) data — high then-falling lending = shorts covering —
but the NSE SLB feed is Akamai-blocked (`raw_slb` not collected), so this runs on the legs we
DO have and treats SLB as an optional confirmer:

    core      15-day return ≤ −20%  AND  delivery ratio rising  AND  price recovering today
    confirm   (when available) SLB lending was high and is now falling

Without SLB it's an oversold-with-conviction bounce; with SLB it's a true squeeze. Pure
`is_short_squeeze` is unit-tested; the pass reads candles + delivery_conviction. Gated dispatch.
"""
from __future__ import annotations

import sqlite3

import structlog

from ..scheduler.market_hours import is_trading_day, now_ist

log = structlog.get_logger()
JOB_ID = "short_squeeze"
SHORT_SQUEEZE = "short_squeeze"

_FALL_PCT = -20.0          # 15-day drawdown that sets up the squeeze
_LOOKBACK = 15


def is_short_squeeze(ret_15d: float | None, delivery_rising: bool,
                     price_recovering: bool, slb_falling: bool | None = None) -> bool:
    """Core squeeze condition. `slb_falling` is an optional confirmer (None = no SLB data,
    doesn't veto)."""
    if ret_15d is None or ret_15d > _FALL_PCT:
        return False
    if not (delivery_rising and price_recovering):
        return False
    if slb_falling is False:                  # explicit SLB data that contradicts → no squeeze
        return False
    return True


def _ret_15d(conn: sqlite3.Connection, symbol: str) -> tuple[float | None, bool]:
    """(15-day % return, price_recovering) from daily candles."""
    rows = conn.execute(
        "SELECT close FROM raw_intraday_candles WHERE symbol=? AND interval='day' "
        "AND close IS NOT NULL ORDER BY ts DESC LIMIT ?", (symbol, _LOOKBACK + 1)).fetchall()
    closes = [r[0] for r in rows]                       # newest first
    if len(closes) < _LOOKBACK + 1 or not closes[_LOOKBACK]:
        return None, False
    ret = (closes[0] - closes[_LOOKBACK]) / closes[_LOOKBACK] * 100.0
    recovering = len(closes) >= 2 and closes[0] > closes[1]
    return round(ret, 2), recovering


def _delivery_rising(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute(
            "SELECT symbol, delivery_trend, MAX(session_date) FROM delivery_conviction GROUP BY symbol"
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {r[0] for r in rows if (r[1] or "").lower() == "rising"}


def run_short_squeeze_pass(conn: sqlite3.Connection, *, symbols: list[str] | None = None) -> dict:
    from ..indicators.universe import live_universe
    symbols = symbols if symbols is not None else live_universe(conn)
    rising = _delivery_rising(conn)
    hits = []
    for sym in symbols:
        ret, recovering = _ret_15d(conn, sym)
        if is_short_squeeze(ret, sym in rising, recovering):
            hits.append({"symbol": sym, "ret_15d": ret})
            log.info("short_squeeze", symbol=sym, ret_15d=ret)
    return {"symbols": len(symbols), "squeezes": len(hits), "hits": hits}


def register_short_squeeze_job(scheduler, db_path: str) -> str:
    """Daily 18:40 IST (after delivery conviction lands) — trading-day gated."""
    from apscheduler.triggers.cron import CronTrigger

    from ..scheduler.market_hours import IST
    from ..storage.db import open_db

    def _tick():
        if not is_trading_day(now_ist().date()):
            return
        conn = open_db(db_path)
        try:
            rep = run_short_squeeze_pass(conn)
            if rep["squeezes"]:
                log.info("short_squeeze_tick", **rep)
        except Exception:
            log.exception("short_squeeze_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=18, minute=40, timezone=IST),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True)
    return JOB_ID
