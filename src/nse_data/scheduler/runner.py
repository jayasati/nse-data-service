"""APScheduler bootstrap + the per-run collector lifecycle.

`make_runner` builds the callable that actually runs one collector against a
fresh DB connection. It's shared by the live scheduler (main.py) and the
catch-up path (scheduler/catchup.py) so both honour the exact same lifecycle
(per-invocation connection, dedup injection, bhavcopy publish-window retry).
"""

from __future__ import annotations

import time
from typing import Any, Callable

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler

from ..collectors.base import EventCollector
from ..storage.db import open_db

log = structlog.get_logger()


def make_scheduler() -> BlockingScheduler:
    """Create the scheduler (IST-timezoned, in-memory jobstore)."""
    return BlockingScheduler(timezone="Asia/Kolkata")


def make_runner(session, db_path: str, dedup_cache) -> Callable[[Any], Any]:
    """Return the callable invoked per job.

    Per-run lifecycle:
      - Open a fresh SQLite connection (thread-portability fix from Phase 3)
      - Inject the dedup cache for EventCollectors
      - Run the collector; if it returns 0 rows AND the collector declares
        publish-window semantics (bhavcopy), retry inside the window
    """
    from ..collectors.bhavcopy import Bhavcopy, PUBLISH_RETRY_MAX, PUBLISH_RETRY_DELAY

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
