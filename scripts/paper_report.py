"""Expectancy / R-metric report over the paper_book forward track record (plan R1).

Tells you, per strategy, whether a signal actually makes money: expectancy, profit
factor, payoff ratio, win rate, max drawdown, and a breakdown by exit reason — the
numbers the promote/shelve decision (P4) needs. Win% alone can't tell a positive-
expectancy signal from a coin flip.

    PYTHONPATH=src .venv/bin/python -u scripts/paper_report.py
    PYTHONPATH=src .venv/bin/python -u scripts/paper_report.py --db data/nse.db
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    from nse_data.research.paper_report import report, format_report
    from nse_data.storage.db import open_db

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    args = ap.parse_args()

    conn = open_db(args.db)
    try:
        print(format_report(report(conn)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
