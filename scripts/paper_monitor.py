"""Paper-book monitor — watch the forward track record fill (P4).

Per strategy: current open positions (unrealized, days, score, protective stop), the
closed-trade expectancy + R9 validation verdict, and progress toward ~100 trades.

    PYTHONPATH=src .venv/bin/python -u scripts/paper_monitor.py
    PYTHONPATH=src .venv/bin/python -u scripts/paper_monitor.py --target 100
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    from nse_data.research.paper_monitor import format_monitor, monitor_snapshot
    from nse_data.storage.db import open_db

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--target", type=int, default=100, help="closed-trade significance target")
    args = ap.parse_args()

    conn = open_db(args.db)
    try:
        print(format_monitor(monitor_snapshot(conn, target=args.target)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
