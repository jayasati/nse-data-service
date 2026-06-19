"""Daily factor-snapshot cron (P8 feature store). Writes today's snapshot to
`factor_snapshot` and tops up any forward-return labels that have now matured.

Registered from main.py via `register_factor_snapshot_job` (CronTrigger 18:45 IST,
trading-day gated — after EOD candles land). Mirrors register_regime_job/morning_brief.
"""
from __future__ import annotations

import datetime as _dt

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..scheduler import market_hours
from ..scheduler.market_hours import IST
from ..storage.db import open_db
from .score import as_of_now_epoch
from . import snapshot

log = structlog.get_logger(__name__)
JOB_ID = "factor_snapshot"


def run_factor_snapshot(db_path: str) -> dict:
    """Write today's snapshot + label matured rows. Returns a small report dict."""
    conn = open_db(db_path)
    try:
        ep = as_of_now_epoch()
        snapshot_date = _dt.datetime.fromtimestamp(ep, IST).date().isoformat()
        wrote = snapshot.run_snapshot(conn, snapshot_date, ep, ep)
        labelled = snapshot.label_matured(conn)
        return {"date": snapshot_date, "wrote": wrote,
                "labelled": sum(labelled.values())}
    finally:
        conn.close()


def register_factor_snapshot_job(scheduler: BlockingScheduler, db_path: str) -> str:
    """Attach the 18:45-IST daily factor-snapshot job. Trading-day gated."""
    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            log.info("factor_snapshot_skipped_non_trading_day")
            return
        try:
            report = run_factor_snapshot(db_path)
            log.info("factor_snapshot", **report)
        except Exception:
            log.exception("factor_snapshot_failed")

    scheduler.add_job(
        _tick,
        trigger=CronTrigger(hour=18, minute=45, timezone=IST),
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return JOB_ID
