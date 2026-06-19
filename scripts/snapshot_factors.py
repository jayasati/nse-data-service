"""P8 — write factor snapshots to `factor_snapshot` (the daily feature store).

    PYTHONPATH=src .venv/bin/python -u scripts/snapshot_factors.py            # today
    PYTHONPATH=src .venv/bin/python -u scripts/snapshot_factors.py --date 2025-01-15
    # backfill history, point-in-time, weekly cadence, then label matured rows:
    PYTHONPATH=src .venv/bin/python -u scripts/snapshot_factors.py --from 2025-06-01 --cadence 5 --label

Run once per trading day after close (cron). POINT-IN-TIME: for each date the as-of
moment is that day's 15:35 IST close, and every engine only reads data dated on/before
it (candle ts <= as_of, result broadcast_dt <= as_of) — never any future bar/filing.
Forward-return labels are filled by scripts/label_snapshots.py (or --label here).
Safe to re-run a date — features overwrite, labels persist.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
import time as _time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def _eod_ep(datestr: str) -> int:
    d = _dt.date.fromisoformat(datestr)
    return int(_dt.datetime(d.year, d.month, d.day, 15, 35, tzinfo=_IST).timestamp())


def _trading_dates(conn, lo: str | None, hi: str | None) -> list[str]:
    """Distinct IST trading dates from the NIFTYBEES daily calendar in [lo, hi]."""
    rows = conn.execute(
        "SELECT DISTINCT date(ts,'unixepoch','+05:30') d FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' ORDER BY d").fetchall()
    return [r[0] for r in rows if (not lo or r[0] >= lo) and (not hi or r[0] <= hi)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (IST); default = today")
    ap.add_argument("--from", dest="from_", default=None, help="backfill range start YYYY-MM-DD")
    ap.add_argument("--to", default=None, help="backfill range end YYYY-MM-DD (default = latest)")
    ap.add_argument("--cadence", type=int, default=5, help="every Nth trading day in the range")
    ap.add_argument("--label", action="store_true", help="fill matured forward labels after writing")
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import snapshot
    from nse_data.research.score import as_of_now_epoch

    conn = open_db(args.db)
    computed_epoch = as_of_now_epoch()

    if args.from_:                               # ---- point-in-time range backfill ----
        cal = _trading_dates(conn, args.from_, args.to)
        dates = cal[::max(1, args.cadence)]
        print(f"backfill: {len(dates)} dates {dates[0]}..{dates[-1]} (cadence {args.cadence}), PIT")
        for i, d in enumerate(dates, 1):
            t0 = _time.time()
            n = snapshot.run_snapshot(conn, d, computed_epoch, _eod_ep(d))
            print(f"  [{i}/{len(dates)}] {d}: {n} rows ({_time.time()-t0:.0f}s)", flush=True)
        if args.label:
            filled = snapshot.label_matured(conn)
            print("labelled: " + "  ".join(f"{h}d:+{v}" for h, v in filled.items()))
    else:                                        # ---- single date (default: today) ----
        if args.date:
            as_of_ep, snapshot_date = _eod_ep(args.date), args.date
        else:
            as_of_ep = computed_epoch
            snapshot_date = _dt.datetime.fromtimestamp(as_of_ep, _IST).date().isoformat()
        n = snapshot.run_snapshot(conn, snapshot_date, computed_epoch, as_of_ep)
        print(f"factor_snapshot: wrote {n} rows for {snapshot_date}")

    total = conn.execute("SELECT COUNT(*) FROM factor_snapshot").fetchone()[0]
    ndates = conn.execute("SELECT COUNT(DISTINCT snapshot_date) FROM factor_snapshot").fetchone()[0]
    print(f"store now {total} rows over {ndates} dates")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
