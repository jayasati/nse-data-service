"""Catch-up runner — recover daily/weekly collectors missed while the host was off.

This project isn't deployed on an always-on host: when the laptop sleeps, the
scheduler dies, and any daily/weekly run scheduled for that window never fires
(APScheduler can't trigger a job while the process is dead). Intraday feeds
self-heal on the next tick, but a once-a-day collector can sit stale for days.

On boot, `main.py` calls `run_due` to immediately run any daily/weekly collector
whose stored data lags its last expected run — reusing the same freshness
verdict the health dashboard shows (`ops.health`). The same logic backs the
manual `scripts/run_collectors.py` tool.

NOTE: this recovers a *missed schedule*, not lost history. Snapshot endpoints
(e.g. /api/fiidiiTradeReact) only serve the latest day, so a run missed two days
ago captures *today's* value, not the gap. Backfilling true history needs a
historical source, not this.
"""

from __future__ import annotations

import structlog

from ..ops import health
from ..scheduler.market_hours import now_ist
from ..scheduler.runner import make_runner
from .jobs import _load_collector

log = structlog.get_logger()

# Cadences worth catching up. Intraday feeds resume on their next tick, so we
# only chase the once-a-day / once-a-week ones.
_CATCHUP_CADENCES = {"daily", "weekly"}
# Verdicts that mean "a scheduled run was missed and re-running helps now".
# 'no_table' is excluded — that needs a migration, not a run.
_DUE_STATUSES = {"stale", "down", "empty"}


def due_collectors(conn, endpoints: dict, now=None) -> list[str]:
    """Names of enabled daily/weekly collectors whose data lags expectations."""
    now = now or now_ist()
    due: list[str] = []
    for name, cfg in endpoints.items():
        if not cfg.get("enabled"):
            continue
        if cfg.get("cadence") not in _CATCHUP_CADENCES:
            continue
        verdict = health.collector_health(conn, name, cfg, now)
        if verdict["status"] in _DUE_STATUSES:
            due.append(name)
    return due


def run_due(session, db_path: str, endpoints: dict, dedup_cache,
            conn, names: list[str] | None = None) -> list[str]:
    """Run the catch-up set once. `names` overrides auto-detection (manual tool).

    `conn` is a read-only connection used only to detect what's due; each run
    opens its own writable connection via the shared runner. Returns the names
    that were run.
    """
    targets = names if names is not None else due_collectors(conn, endpoints)
    if not targets:
        log.info("catchup_none_due")
        return []

    runner = make_runner(session, db_path, dedup_cache)
    log.info("catchup_start", collectors=targets)
    for name in targets:
        cfg = endpoints.get(name)
        if not cfg or not cfg.get("collector"):
            log.warning("catchup_skip", collector=name, reason="unknown_or_no_spec")
            continue
        try:
            collector = _load_collector(cfg["collector"])
            collector.name = name
            if cfg.get("table"):
                collector.table = cfg["table"]
            runner(collector)   # logs its own collector_run / collector_failed
        except Exception:
            log.exception("catchup_load_failed", collector=name)
    log.info("catchup_done", collectors=targets)
    return targets
