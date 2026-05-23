"""APScheduler entry point for the parser pipeline.

Bridges between the Layer 2 collector contract (run(session, db) returning
a structured report) and Layer 3's parser job orchestrator. The scheduler
calls .run() on this class every N minutes; we pull a batch of pending
rows and process them.

Why a wrapper class instead of registering parsers/job.py:run_job directly:
  - The scheduler instantiates with cls() — needs a no-arg constructor.
  - Each invocation gets a fresh DB connection (LEARNINGS.md on thread
    portability).
  - We want a consistent `name` attribute for circuit-breaker accounting
    and observability, settable post-construction.
  - run() returns the same shape as Layer 2 collectors' reports — keeps
    operational tooling uniform.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nse_data.parsers.job import JobReport, run_job

LOG = logging.getLogger(__name__)

# Tuneable — how many rows the parser tries to process per tick.
# 50 rows × 10-min cadence = 300 rows/hr in steady state, well within
# NSE-polite limits. Higher batch size means longer ticks; lower means
# wasted scheduler overhead.
DEFAULT_BATCH_SIZE = 50

# Where archived PDFs live. Hardcoded — same convention as Layer 2's
# bhavcopy/db_backups paths.
ARCHIVE_ROOT = Path("data/archive")

# Sqlite path. Matches Layer 2's convention; main.py passes this to
# collectors via the runner.
DEFAULT_DB_PATH = Path("data/nse.db")


@dataclass
class ParserRunReport:
    """Report shape matching what Layer 2 collectors emit.

    Wraps the inner JobReport so the scheduler logs see a familiar shape:
    `succeeded`, `failed`, `rows_seen`, `persist` (compatibility), errors.
    """

    collector: str
    started_at: float
    finished_at: float
    duration_ms: int
    fetched: int        # number of rows pulled for processing
    succeeded: int      # rows that reached a clean terminal state
    failed: int         # rows whose pipeline raised an unhandled error
    rows_seen: int      # alias of fetched, for ops-tool compatibility
    by_terminal_state: dict[str, int]
    errors: list[str] = field(default_factory=list)


class ParserJob:
    """Scheduler-facing class for the parser pipeline.

    Registered in endpoints.yaml as:

        pdf_parser:
          collector: nse_data.parsers.scheduler_job:ParserJob
          cadence: 10m
          active_hours: "08:00-19:30"
          enabled: true
    """

    # Set by the dispatcher in jobs.py after construction.
    name: str = "pdf_parser"

    # Optional overrides. Kept as class attrs so endpoints.yaml could one
    # day add a `batch_size: 100` config key without touching code.
    batch_size: int = DEFAULT_BATCH_SIZE
    db_path: Path = DEFAULT_DB_PATH
    archive_root: Path = ARCHIVE_ROOT

    def run(self, session, db=None) -> ParserRunReport:
        """Called by main.py's runner. Process up to batch_size pending rows.

        Args:
            session: Shared SessionManager (Layer 1).
            db: Ignored. The scheduler may pass a connection but we follow
                Layer 2's per-invocation-connection pattern to avoid
                thread-portability bugs (see LEARNINGS.md).

        Returns:
            ParserRunReport with structured outcome counts.
        """
        started = time.time()

        # Per-invocation connection — see LEARNINGS.md "thread-portable
        # connections" lesson from Phase 3.
        own_db = sqlite3.connect(self.db_path)
        try:
            inner: JobReport = run_job(
                db=own_db,
                session=session,
                archive_root=self.archive_root,
                limit=self.batch_size,
            )
        finally:
            own_db.close()

        finished = time.time()
        succeeded = sum(inner.by_terminal_state.values())
        failed = len(inner.errors)

        report = ParserRunReport(
            collector=self.name,
            started_at=started,
            finished_at=finished,
            duration_ms=int((finished - started) * 1000),
            fetched=inner.rows_processed,
            succeeded=succeeded,
            failed=failed,
            rows_seen=inner.rows_processed,
            by_terminal_state=dict(inner.by_terminal_state),
            errors=list(inner.errors),
        )

        LOG.info(
            "parser_job_done collector=%s rows=%d duration_ms=%d states=%s",
            self.name, report.rows_seen, report.duration_ms,
            report.by_terminal_state,
        )
        return report