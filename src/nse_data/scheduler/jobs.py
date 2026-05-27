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

import importlib
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Callable

from apscheduler.triggers.cron import CronTrigger

from nse_data.parsers.job import JobReport, run_job
from nse_data.scheduler import market_hours

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


# ============================================================================
# Job registration — endpoints.yaml -> APScheduler cron triggers
# ============================================================================
#
# main.py and scripts/run_once.py import register_jobs / _load_collector from
# here. This is the bridge that turns each enabled endpoints.yaml entry into
# one (or more) scheduled APScheduler jobs.
#
# Two-part contract per entry:
#   trigger — WHEN the job fires (cadence / run_at), built as a CronTrigger.
#   gate    — WHETHER it actually runs when it fires (market_hours_only /
#             active_hours / trading_day_only), checked at runtime so it can
#             account for holidays the cron expression can't know about.
#
# A runner callable (collector) -> None is supplied by the caller; we wrap it
# with the gate and hand the wrapped closure to the scheduler.

# Interval cadences -> crontab minute field. Hours are bounded separately.
_INTERVAL_MINUTE = {
    "1m": "*",
    "3m": "*/3",
    "5m": "*/5",
    "10m": "*/10",
    "30m": "*/30",
}

# Sub-minute cadences -> crontab second field. Used by tight pre-open polls
# (e.g. GIFT Nifty every 30s). Always paired with an active_hours window so the
# high-frequency fire is confined; the runtime gate still trims the exact edges.
_INTERVAL_SECOND = {
    "15s": "*/15",
    "30s": "*/30",
}

# Default misfire grace: how late a fire can be and still run (BlockingScheduler
# can fall behind during a long job). Intraday jobs want a tight grace so a
# delayed 5-min tick doesn't pile up; daily/weekly jobs tolerate more.
_GRACE_INTERVAL = 120
_GRACE_DAILY = 1800

_DOW = {"mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu",
        "fri": "fri", "sat": "sat", "sun": "sun"}


def _load_collector(spec: str):
    """Instantiate a collector from a 'module.path:ClassName' spec string.

    Does not set .name / .table — the caller (register_jobs or run_once) owns
    that, since the canonical name is the endpoints.yaml key, not the class.
    """
    module_path, _, cls_name = spec.partition(":")
    if not module_path or not cls_name:
        raise ValueError(f"bad collector spec {spec!r}; expected 'module:Class'")
    module = importlib.import_module(module_path)
    return getattr(module, cls_name)()


def _parse_hhmm(s: str) -> tuple[int, int]:
    """'09:08' -> (9, 8). Raises ValueError on malformed input."""
    h, m = s.strip().split(":")
    return int(h), int(m)


def _run_at_list(run_at: Any) -> list[str]:
    """Normalize run_at (str or list) to a list of time tokens."""
    if run_at is None:
        return []
    if isinstance(run_at, str):
        return [run_at]
    if isinstance(run_at, (list, tuple)):
        return [str(x) for x in run_at]
    raise ValueError(f"run_at must be str or list, got {type(run_at).__name__}")


def _hour_bound(cfg: dict) -> str:
    """Bound an interval trigger's hours to its active window when known.

    Reduces needless wake-ups (a 5-min job otherwise fires 288×/day). The
    runtime gate is still the source of truth for the exact minute and for
    holidays — this is purely an optimization, so it errs wide.
    """
    if cfg.get("market_hours_only"):
        return "9-15"   # market 09:15–15:30; gate trims the edges
    ah = cfg.get("active_hours")
    if ah:
        start, end = ah.split("-")
        return f"{_parse_hhmm(start)[0]}-{_parse_hhmm(end)[0]}"
    return "*"


def build_triggers(cfg: dict) -> list[tuple[str, CronTrigger]]:
    """Return [(job_id_suffix, trigger)] for one endpoint config.

    Most entries yield one trigger (suffix ''). A multi-time daily run_at
    (e.g. call_auction at 09:05 + 10:05) yields one per time, each suffixed
    '@HH:MM' so job ids stay unique.
    """
    tz = market_hours.IST
    cadence = cfg.get("cadence")

    if cadence in _INTERVAL_SECOND:
        return [("", CronTrigger(
            second=_INTERVAL_SECOND[cadence], minute="*",
            hour=_hour_bound(cfg), timezone=tz,
        ))]

    if cadence in _INTERVAL_MINUTE:
        return [("", CronTrigger(
            minute=_INTERVAL_MINUTE[cadence], hour=_hour_bound(cfg), timezone=tz,
        ))]

    if cadence == "1h":
        return [("", CronTrigger(
            minute=0, hour=_hour_bound(cfg), timezone=tz,
        ))]

    if cadence == "daily":
        tokens = _run_at_list(cfg.get("run_at"))
        if not tokens:
            raise ValueError("daily cadence requires run_at")
        out = []
        multi = len(tokens) > 1
        for tok in tokens:
            h, m = _parse_hhmm(tok)
            suffix = f"@{h:02d}:{m:02d}" if multi else ""
            out.append((suffix, CronTrigger(hour=h, minute=m, timezone=tz)))
        return out

    if cadence == "weekly":
        tokens = _run_at_list(cfg.get("run_at"))
        if not tokens:
            raise ValueError("weekly cadence requires run_at")
        out = []
        multi = len(tokens) > 1
        for tok in tokens:
            dow_str, hhmm = tok.split()
            dow = _DOW.get(dow_str.strip().lower())
            if dow is None:
                raise ValueError(f"bad weekly run_at day-of-week: {tok!r}")
            h, m = _parse_hhmm(hhmm)
            suffix = f"@{dow}{h:02d}:{m:02d}" if multi else ""
            out.append((suffix, CronTrigger(
                day_of_week=dow, hour=h, minute=m, timezone=tz,
            )))
        return out

    raise ValueError(f"unknown cadence {cadence!r}")


def make_gate(cfg: dict) -> Callable[[], bool]:
    """Build the runtime predicate deciding whether a fired job should run.

    Conditions are ANDed. Evaluated against now_ist() at fire time so holiday
    awareness (which a cron expression lacks) is honored.

      market_hours_only: true  -> is_market_open()  (trading day + 09:15–15:30)
      trading_day_only:  true  -> is_trading_day()   (skip weekends/holidays)
      active_hours: "A-B"      -> A <= now.time() <= B  (window only)

    No conditions -> always run (the cron trigger's time is the only gate);
    used by weekly Sunday jobs, which must NOT be trading-day gated.
    """
    checks: list[Callable[[datetime], bool]] = []

    if cfg.get("market_hours_only"):
        checks.append(lambda now: market_hours.is_market_open(now))
    if cfg.get("trading_day_only"):
        checks.append(lambda now: market_hours.is_trading_day(now.date()))

    ah = cfg.get("active_hours")
    if ah:
        start_s, end_s = ah.split("-")
        start = dtime(*_parse_hhmm(start_s))
        end = dtime(*_parse_hhmm(end_s))
        checks.append(lambda now, s=start, e=end: s <= now.timetz().replace(tzinfo=None) <= e)

    def gate() -> bool:
        now = market_hours.now_ist()
        return all(check(now) for check in checks)

    return gate


def _make_job(runner: Callable[[Any], Any], collector, gate, job_id: str):
    """Wrap the runner in its gate. Returns the zero-arg callable APScheduler runs."""
    def job():
        if not gate():
            LOG.info("job_skipped_gate id=%s", job_id)
            return
        runner(collector)
    return job


def register_jobs(scheduler, endpoints: dict, runner: Callable[[Any], Any]) -> list[str]:
    """Register every enabled endpoint as a scheduled job. Returns job ids.

    Args:
        scheduler: an APScheduler scheduler (BlockingScheduler from runner.py).
        endpoints: the mapping from settings.load_endpoints().
        runner:    callable taking a collector instance and running it once
                   (main.py builds this with the shared session + db path).

    A malformed or unschedulable entry is logged and skipped rather than
    aborting the whole boot — one bad config line shouldn't ground the fleet.
    """
    registered: list[str] = []

    for name, cfg in endpoints.items():
        if not cfg.get("enabled"):
            continue
        spec = cfg.get("collector")
        if not spec:
            LOG.warning("skip endpoint=%s reason=no_collector", name)
            continue

        try:
            collector = _load_collector(spec)
            collector.name = name
            if cfg.get("table"):
                collector.table = cfg["table"]
            triggers = build_triggers(cfg)
        except Exception as e:
            LOG.error("skip endpoint=%s reason=%s", name, e)
            continue

        gate = make_gate(cfg)
        cadence = cfg.get("cadence")
        if cadence in _INTERVAL_SECOND:
            grace = 15   # tight — a late 30s tick should drop, not pile up
        elif cadence in _INTERVAL_MINUTE or cadence == "1h":
            grace = _GRACE_INTERVAL
        else:
            grace = _GRACE_DAILY

        for suffix, trigger in triggers:
            job_id = f"{name}{suffix}"
            scheduler.add_job(
                _make_job(runner, collector, gate, job_id),
                trigger=trigger,
                id=job_id,
                name=job_id,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=grace,
                replace_existing=True,
            )
            registered.append(job_id)

    LOG.info("registered_jobs count=%d ids=%s", len(registered), registered)
    return registered