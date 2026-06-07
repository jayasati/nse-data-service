"""
Live intraday-indicator scheduler hook.

While the market is open, this job fires every minute and recomputes every
registered intraday indicator across the FNO + Nifty 500 universe. The
in-progress 5-min bar gets overwritten ~5 times before it closes (cheap;
the math is fast) — which is what gives signals their <1-minute latency.

Outside market hours the job is a no-op: the `is_market_open()` gate returns
immediately without opening a DB connection. APScheduler keeps the trigger
armed at all times; the gate does the filtering.

Registered from `main.py` alongside the collector jobs. Lives here, not in
`nse_data/scheduler/`, because everything about this job (universe, cadence
filtering, retention) is indicator-package concerns.
"""

from __future__ import annotations

import time

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..scheduler.market_hours import is_market_open
from ..storage.db import open_db
from .compute import run_all
from .live_snapshot import run_snapshot_pass
from .universe import live_universe

log = structlog.get_logger()

JOB_ID = "indicators_intraday"
_INTERVAL_SECONDS = 60


def run_intraday_pass(db_path: str) -> dict:
    """One pass over the intraday universe. Returns a small report dict.

    Two stages on the same connection: first recompute the per-bar 5-min
    indicator series (RSI/MACD/VWAP), then roll those — plus daily ATR and the
    SMA regime — into the per-symbol `indicator_live` snapshot and mirror it to
    Redis. The snapshot reads what the first stage just wrote, so order matters.
    """
    if not is_market_open():
        return {"skipped": "market_closed"}

    started = time.time()
    redis_client = _connect_redis()
    conn = open_db(db_path)
    try:
        symbols = live_universe(conn)
        results = run_all(conn, symbols, cadence="intraday")
        snapshot = run_snapshot_pass(conn, symbols, redis_client=redis_client)
    finally:
        conn.close()

    by_ind: dict[str, int] = {}
    for r in results:
        by_ind[r.indicator] = by_ind.get(r.indicator, 0) + r.rows_written
    return {
        "elapsed_secs": round(time.time() - started, 2),
        "symbols": len(symbols),
        "rows_written": by_ind,
        "snapshot": snapshot,
    }


def _connect_redis():
    """Best-effort Redis client for the snapshot mirror; None if unavailable.

    Mirrors main.py's discovery (default localhost, decoded responses). A down
    Redis is not fatal — the snapshot still lands in SQLite and the signal
    engine falls back to reading there; we just lose the hot-path cache.
    """
    try:
        import redis  # type: ignore

        client = redis.Redis(decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None


def register_live_job(scheduler: BlockingScheduler, db_path: str) -> str:
    """Attach the every-minute live-indicator job to the running scheduler."""
    def _tick():
        try:
            report = run_intraday_pass(db_path)
            # Only log when we actually did work, otherwise every minute would
            # emit a "skipped market_closed" line and drown out useful events.
            if "skipped" not in report:
                log.info("indicators_intraday_tick", **report)
        except Exception:
            log.exception("indicators_intraday_failed")

    scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(seconds=_INTERVAL_SECONDS),
        id=JOB_ID,
        # Don't pile up — if a tick somehow takes >60s, skip the next one
        # rather than running concurrently against the same DB.
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return JOB_ID
