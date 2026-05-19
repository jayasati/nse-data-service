"""APScheduler bootstrap. Plain BlockingScheduler for Phase 2."""

from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler


def make_scheduler() -> BlockingScheduler:
    """
    Create the scheduler. In-memory jobstore is fine while we have one job —
    we add a persistent SQLite jobstore when there's >1 job and missed-run
    semantics start to matter (Phase 7).
    """
    return BlockingScheduler(timezone="Asia/Kolkata")