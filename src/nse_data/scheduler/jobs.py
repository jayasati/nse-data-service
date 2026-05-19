"""
Translate endpoints.yaml entries into APScheduler jobs.

Each enabled endpoint becomes one scheduled job whose callable is the
`runner` passed in by main.py. The runner receives the collector instance
and is responsible for calling collector.run(session, db) and logging.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Mapping

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


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

        trigger = _trigger_for(cfg)
        scheduler.add_job(
            func=runner,
            args=(collector,),
            trigger=trigger,
            id=name,
            replace_existing=True,
            misfire_grace_time=60,
        )
        registered.append(name)
    return registered


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
        return CronTrigger(hour=int(h), minute=int(m))

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
            )
        return IntervalTrigger(minutes=every)

    if cadence.endswith("h"):
        every = int(cadence[:-1])
        return IntervalTrigger(hours=every)

    raise ValueError(f"Unknown cadence: {cadence!r}")