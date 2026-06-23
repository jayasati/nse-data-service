#!/usr/bin/env python
"""One-off purge of the DEAD interval='5minute' rows in raw_intraday_candles (frozen at 2025-06-11;
unused — read_intraday_5m / ORB / indicators all read interval='minute'). The DELETE scans the 59M-row
table (no index on `interval` alone), so it must NOT run during market hours (lock contention + WAL
bloat — see deployment-aws WAL gotcha). This script is cron'd for ~23:15 IST when collectors are idle,
checkpoints the WAL afterward, and SELF-DISABLES via a sentinel so it scans only once.

Cron (server-local IST): 15 23 * * *  cd /opt/nse-data-service && PYTHONPATH=src .venv/bin/python scripts/purge_dead_5min.py
"""
from __future__ import annotations

import os
import sqlite3
import time

SENTINEL = "data/.5min_purged"


def main():
    if os.path.exists(SENTINEL):
        return  # already done — fast no-op, no scan
    c = sqlite3.connect("data/nse.db", timeout=120)
    c.execute("PRAGMA busy_timeout=120000")
    t0 = time.time()
    cur = c.execute("DELETE FROM raw_intraday_candles WHERE interval='5minute'")
    n = cur.rowcount
    c.commit()
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    with open(SENTINEL, "w") as f:
        f.write(f"purged {n} rows at epoch {int(time.time())}\n")
    print(f"purged {n} dead 5minute rows in {time.time()-t0:.0f}s; WAL checkpointed; sentinel written")


if __name__ == "__main__":
    main()
