"""
Translate endpoints.yaml entries into APScheduler jobs.

Each enabled endpoint becomes one scheduled job whose callable is the
`runner` passed in by main.py. The runner receives the collector instance
and is responsible for calling collector.run(session, db) and logging.

Phase 3 addition: entries with market_hours_only: true get their runner
wrapped in a market-hours gate. The cron trigger still fires every N min,
but the gate short-circuits to a no-op outside 09:15-15:30 IST on
trading days. Two layers — trigger decides *when to attempt*, gate
decides *whether to proceed*.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable, Mapping

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from zoneinfo import ZoneInfo
from .market_hours import is_market_open

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger(__name__)


def register_jobs(
    scheduler,
    endpoints: Mapping[str, dict],
    runner: Callable[[Any], None],
) -> list[str]:
    """
    For each enabled endpoint, instantiate its collector and register a job.
    Returns the list of registered job names.
    """
    registered: list[str] = []
    for name, cfg in endpoints.items():
        if not cfg.get("enabled", False):
            continue

        collector = _load_collector(cfg["collector"])
        # Endpoint key in YAML wins over class default — keeps endpoint_name
        # consistent with config (used for circuit/rate-limit keys in Layer 1).
        collector.name = name

        # Wrap the runner if this endpoint is market-hours-only
        if cfg.get("market_hours_only"):
            wrapped = _market_hours_gate(runner, name)
        else:
            wrapped = runner

        trigger = _trigger_for(cfg)
        scheduler.add_job(
            func=wrapped,
            args=(collector,),
            trigger=trigger,
            id=name,
            replace_existing=True,
            misfire_grace_time=60,
        )
        registered.append(name)
    return registered


def _market_hours_gate(runner: Callable, name: str) -> Callable:
    """
    Wrap a runner so it no-ops outside NSE market hours.

    Why a gate and not a fancier cron expression: APScheduler's cron parser
    doesn't speak NSE holidays. Encoding "every 5 minutes on trading days"
    into cron alone would require duplicating the holiday list inside the
    cron expression. Cleaner to let the trigger be naive and the gate be
    smart — single source of truth in market_hours.py.
    """
    def gated(collector):
        if not is_market_open():
            log.debug("market_closed_skip endpoint=%s", name)
            return
        return runner(collector)
    return gated


def _load_collector(spec: str):
    """Resolve a 'pkg.module:ClassName' string into an instance."""
    if ":" not in spec:
        raise ValueError(f"collector spec must be 'module:Class', got {spec!r}")
    module_path, class_name = spec.split(":", 1)
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls()


def _trigger_for(cfg: Mapping[str, Any]):
    """
    Convert cadence + active_hours into an APScheduler trigger.

    Supported cadences:
        "Nm"     - every N minutes; respects active_hours if present
        "Nh"     - every N hours
        "daily"  - once per day at run_at (HH:MM, default 00:00)
    """
    cadence = (cfg.get("cadence") or "").strip()

    if cadence == "daily":
        run_at = cfg.get("run_at") or "00:00"
        h, m = run_at.split(":")
        return CronTrigger(hour=int(h), minute=int(m),timezone=IST)

    if cadence.endswith("m"):
        every = int(cadence[:-1])
        active = cfg.get("active_hours")
        if active:
            start, end = active.split("-")
            start_h = int(start.split(":")[0])
            end_h = int(end.split(":")[0])
            return CronTrigger(
                hour=f"{start_h}-{end_h}",
                minute=f"*/{every}",
                timezone=IST,
            )
        return IntervalTrigger(minutes=every)

    if cadence.endswith("h"):
        every = int(cadence[:-1])
        return IntervalTrigger(hours=every)

    raise ValueError(f"Unknown cadence: {cadence!r}")