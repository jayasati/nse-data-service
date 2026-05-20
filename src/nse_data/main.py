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
import time 

from .collectors.base import EventCollector
from .scheduler.jobs import register_jobs
from .scheduler.runner import make_scheduler
from .session.manager import SessionManager
from .settings import load_endpoints
from .storage.cache import MemoryDedupCache, RedisDedupCache
from .storage.db import apply_migrations, open_db


log = structlog.get_logger()


def _make_runner(session, db_path, dedup_cache):
    """
    Returns the callable APScheduler invokes per job.

    Per-run lifecycle:
      - Open a fresh SQLite connection (thread-portability fix from Phase 3)
      - Inject the dedup cache for EventCollectors
      - Run the collector; if it returns 0 rows AND the collector declares
        publish-window semantics, retry inside the window
    """
    from .collectors.bhavcopy import Bhavcopy, PUBLISH_RETRY_MAX, PUBLISH_RETRY_DELAY

    def run_collector(collector):
        if isinstance(collector, EventCollector):
            collector.dedup_cache = dedup_cache

        conn = open_db(db_path)
        try:
            report = collector.run(session, conn)
            log.info("collector_run", **report.to_dict())

            # Publish-window retry: bhavcopy may 404 for ~30 min after run_at
            if isinstance(collector, Bhavcopy) and report.rows_seen == 0:
                for attempt in range(1, PUBLISH_RETRY_MAX):
                    log.info("bhavcopy_retry", attempt=attempt)
                    time.sleep(PUBLISH_RETRY_DELAY)
                    report = collector.run(session, conn)
                    log.info("collector_run", **report.to_dict())
                    if report.rows_seen > 0:
                        break
        except Exception:
            log.exception("collector_failed", collector=collector.name)
        finally:
            conn.close()
    return run_collector


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

    scheduler = make_scheduler()
    registered = register_jobs(
        scheduler, endpoints, _make_runner(session, db_path, dedup_cache)
    )
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