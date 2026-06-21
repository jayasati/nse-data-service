#!/usr/bin/env python3
"""Run the weekly positional ranking once: rank → overlay → size → snapshot → paper-rebalance.

    PYTHONPATH=src python scripts/weekly_positional.py            # default top-15 basket
    PYTHONPATH=src python scripts/weekly_positional.py --top 20

Writes a weekly_ranking snapshot (visible at /rankings) and opens/rotates the paper basket.
The scheduler runs this automatically every Monday 09:00 IST.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nse_data.research.weekly_positional import run_weekly_positional  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--top", type=int, default=15, help="basket size (sized names)")
    args = ap.parse_args()
    report = run_weekly_positional(args.db, top_n=args.top)
    print("weekly_positional:", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
