"""
Process entrypoint. Starts the scheduler with the announcements collector
running on a 5m cron from 08:00-19:00 IST.

    python -m nse_data.main

Logs to stdout as JSON (structlog). systemd captures and rotates.
"""

from __future__ import annotations

import logging
import sys

import structlog

from .indicators.live_job import register_live_job
from .indicators.pre_market_loader import register_pre_market_loader
from .market.regime_job import register_regime_job
from .signals.detect import register_signal_job
from .signals.outcome_labeler import register_outcome_labeler
from .signals.paper_tracker import register_paper_tracker
from .scheduler.catchup import run_due
from .scheduler.jobs import register_jobs
from .scheduler.runner import make_runner, make_scheduler
from .session.manager import SessionManager
from .settings import load_endpoints
from .storage.cache import MemoryDedupCache, RedisDedupCache
from .storage.db import apply_migrations, open_db


log = structlog.get_logger()


def _build_dedup_cache():
    """Prefer Redis; fall back to in-process memory cache if unavailable."""
    try:
        import redis  # type: ignore
        r = redis.Redis(decode_responses=True)
        r.ping()
        log.info("dedup_cache_redis_ok")
        return RedisDedupCache(r, namespace="announcements_equity")
    except Exception as e:
        log.warning("dedup_cache_redis_unavailable", error=str(e))
        return MemoryDedupCache()


def main() -> int:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    endpoints = load_endpoints("config/endpoints.yaml")

    # One-time migration pass at startup. Use a dedicated connection that's
    # closed immediately — the long-lived `db` of before is gone, because
    # APScheduler runs jobs in worker threads and SQLite connections aren't
    # thread-portable. See _make_runner: each job opens its own connection.
    db_path = "data/nse.db"
    mig_conn = open_db(db_path)
    newly = apply_migrations(mig_conn)
    mig_conn.close()
    if newly:
        log.info("migrations_applied", files=newly)

    session = SessionManager()
    dedup_cache = _build_dedup_cache()

    # Catch-up pass: this host isn't always-on, so a daily/weekly run scheduled
    # while the laptop was asleep never fired. Before starting the live loop,
    # run any daily/weekly collector whose data lags its last expected run.
    # (Recovers a missed schedule, not lost history — see scheduler/catchup.py.)
    cu_conn = open_db(db_path)
    try:
        run_due(session, db_path, endpoints, dedup_cache, cu_conn)
    except Exception:
        log.exception("catchup_failed")  # never let catch-up block the scheduler
    finally:
        cu_conn.close()

    scheduler = make_scheduler()
    registered = register_jobs(
        scheduler, endpoints, make_runner(session, db_path, dedup_cache)
    )
    # Live intraday-indicator compute: every minute during market hours, sweeps
    # the FNO + Nifty 500 universe, writes to indicator_*_5m. Gated internally
    # on is_market_open() so off-hours ticks are cheap no-ops.
    registered.append(register_live_job(scheduler, db_path))
    # Pre-market loader: 08:45 IST on trading days, seeds indicator_live with the
    # previous session's values and publishes the blacklist + quality flags to
    # Redis before the 09:15 open.
    registered.append(register_pre_market_loader(scheduler, db_path))
    # Signal detector: every minute during market hours, sweeps the same
    # FNO + Nifty 500 universe, applies hard gates + the Phase-1 rules, and
    # writes fresh signals (+ their feature snapshot). Gated internally on
    # is_market_open(); reads the indicator_live snapshot the live job just wrote.
    registered.append(register_signal_job(scheduler, db_path))
    # Paper-trade tracker: every minute during market hours, opens a paper trade
    # per new signal (ATR bracket) and closes any that hit T1/SL, force-flatting
    # the rest at 15:20. Internally gated on is_market_open().
    registered.append(register_paper_tracker(scheduler, db_path))
    # Outcome labeler: nightly at 19:30 IST (trading days), fills signal_outcomes
    # with forward returns + MAE/MFE — the label side of the ML dataset.
    registered.append(register_outcome_labeler(scheduler, db_path))
    # Market regime classifier: every 5 minutes during market hours, snapshots
    # VIX/Nifty/breadth/GIFT into market_state with an overall_regime tag the
    # confidence scorer reads (Phase 2, Week 7). Internally gated on market hours.
    registered.append(register_regime_job(scheduler, db_path))
    log.info("scheduler_starting", jobs=registered)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler_stopping")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())