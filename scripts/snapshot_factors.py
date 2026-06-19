"""P8 — write today's factor snapshot to `factor_snapshot` (the daily feature store).

    PYTHONPATH=src .venv/bin/python -u scripts/snapshot_factors.py

Run once per trading day after close (cron). Point-in-time: every engine only reads
data dated on/before the as-of moment. Forward-return labels are filled later by
scripts/label_snapshots.py. Safe to re-run a date — features overwrite, labels persist.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (IST); default = today")
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import snapshot
    from nse_data.research.score import as_of_now_epoch

    if args.date:
        d = _dt.date.fromisoformat(args.date)
        # as-of end of that trading day (15:35 IST close-ish)
        as_of_ep = int(_dt.datetime(d.year, d.month, d.day, 15, 35, tzinfo=_IST).timestamp())
        snapshot_date = args.date
    else:
        as_of_ep = as_of_now_epoch()
        snapshot_date = _dt.datetime.fromtimestamp(as_of_ep, _IST).date().isoformat()
    computed_epoch = as_of_now_epoch()

    conn = open_db(args.db)
    n = snapshot.run_snapshot(conn, snapshot_date, computed_epoch, as_of_ep)
    total = conn.execute("SELECT COUNT(*) FROM factor_snapshot").fetchone()[0]
    dates = conn.execute("SELECT COUNT(DISTINCT snapshot_date) FROM factor_snapshot").fetchone()[0]
    print(f"factor_snapshot: wrote {n} rows for {snapshot_date}  "
          f"(store now {total} rows over {dates} dates)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
