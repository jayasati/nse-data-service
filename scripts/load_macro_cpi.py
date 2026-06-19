"""Set India CPI YoY inflation into raw_macro_market.cpi_yoy (the Macro engine's
inflation input). MANUAL by design — a deliberate, documented choice:

No free source gives the *current headline* CPI automatically from our ap-south-1 host:
  - FRED              → times out from this infra (US-hosted), and lags weeks anyway
  - data.gov.in       → flaky/timeouts AND the CPI resources are stale snapshots (→2023)
  - MOSPI eSankhyiki  → reachable (verify=False past the self-signed cert), signup+login
                        work (login uses `username`=email; data path is /api/cpi/getCPIIndex
                        — both undocumented), BUT the free registered tier returns only a
                        fixed 10-record teaser (no General/All-India/Combined headline);
                        full access is admin-gated ("only admin are authorized to query").
CPI is monthly and a 0.1-weight macro-overlay input, so manual entry is the right tool.
A monthly Telegram reminder (scripts/cpi_reminder.py) nudges the update.

    PYTHONPATH=src .venv/bin/python scripts/load_macro_cpi.py --cpi 6.6     # latest CPI YoY %
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--cpi", type=float, required=True, help="latest India CPI YoY inflation %")
    ap.add_argument("--month", default=None, help="YYYY-MM-01 the print is for (default: this month)")
    args = ap.parse_args()

    from nse_data.storage.db import open_db, apply_migrations
    d = args.month or _dt.date.today().replace(day=1).isoformat()
    conn = open_db(args.db)
    conn.execute("PRAGMA busy_timeout=60000")
    apply_migrations(conn)
    conn.execute("INSERT INTO raw_macro_market (date, cpi_yoy, fetched_at) VALUES (?,?,?) "
                 "ON CONFLICT(date) DO UPDATE SET cpi_yoy=excluded.cpi_yoy, "
                 "fetched_at=excluded.fetched_at", (d, args.cpi, int(time.time())))
    conn.commit()
    conn.close()
    print(f"CPI YoY = {args.cpi}% set for {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
