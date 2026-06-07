"""
Announcement priority classification (FEATURE_CHECKLIST Phase 5, Week 16, 16.2).

Reads unclassified `raw_announcements`, maps each subject to a priority bucket
via `subject_classifier` (config/priority.yaml), and writes it back to the
`priority` column. The 'skip' bucket marks announcements not worth downloading.
"""

from __future__ import annotations

import sqlite3

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..storage.db import open_db
from .subject_classifier import classify_subject

log = structlog.get_logger()
JOB_ID = "announcement_classify"


def run_classification(conn: sqlite3.Connection, *, limit: int = 5000) -> dict:
    """Classify announcements whose priority is still unset. Returns bucket counts."""
    rows = conn.execute(
        "SELECT fingerprint, subject FROM raw_announcements "
        "WHERE priority IS NULL OR priority = '' LIMIT ?",
        (limit,),
    ).fetchall()
    counts: dict[str, int] = {}
    for fingerprint, subject in rows:
        bucket = classify_subject(subject)
        conn.execute(
            "UPDATE raw_announcements SET priority = ? WHERE fingerprint = ?",
            (bucket, fingerprint),
        )
        counts[bucket] = counts.get(bucket, 0) + 1
    conn.commit()
    return {"classified": len(rows), **counts}


def run_classify_job(db_path: str) -> dict:
    conn = open_db(db_path)
    try:
        return run_classification(conn)
    finally:
        conn.close()


def register_classify_job(scheduler: BlockingScheduler, db_path: str) -> str:
    """Every 10 min, classify any newly-collected announcements (task 16.2)."""
    def _tick():
        try:
            report = run_classify_job(db_path)
            if report.get("classified"):
                log.info("announcement_classify", **report)
        except Exception:
            log.exception("announcement_classify_failed")

    scheduler.add_job(
        _tick, trigger=IntervalTrigger(seconds=600),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True,
    )
    return JOB_ID
