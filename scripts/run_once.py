"""
Run a single collector once against live NSE. Bypasses the scheduler.

    python scripts/run_once.py announcements_equity

Useful for smoke testing a newly-added collector and for the Phase 2 exit
criterion: run twice five minutes apart and confirm second run dedups.
"""

from __future__ import annotations

import json
import logging
import sys

from nse_data.collectors.base import EventCollector
from nse_data.scheduler.jobs import _load_collector
from nse_data.session.manager import SessionManager
from nse_data.settings import load_endpoints
from nse_data.storage.cache import MemoryDedupCache
from nse_data.storage.db import apply_migrations, open_db


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) != 2:
        print("usage: run_once.py <endpoint_name>", file=sys.stderr)
        return 2

    name = sys.argv[1]
    endpoints = load_endpoints("config/endpoints.yaml")
    if name not in endpoints:
        print(f"unknown endpoint: {name}", file=sys.stderr)
        return 2

    cfg = endpoints[name]
    collector = _load_collector(cfg["collector"])
    collector.name = name

    db = open_db("data/nse.db")
    apply_migrations(db)

    session = SessionManager()
    if isinstance(collector, EventCollector):
        collector.dedup_cache = MemoryDedupCache()

    try:
        report = collector.run(session, db)
        print(json.dumps(report.to_dict(), indent=2, default=str))
    finally:
        session.close()
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())