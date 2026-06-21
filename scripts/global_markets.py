#!/usr/bin/env python3
"""Fetch the global / cross-asset market snapshot (US, VIX, DXY, crude, gold, Asia, Indian indices).

    PYTHONPATH=src python scripts/global_markets.py

Populates raw_global_markets (Phase-1 context). The scheduler runs this at 08:15 & 17:15 IST.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nse_data.collectors.global_markets import run_global_markets  # noqa: E402


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/nse.db")
    args = ap.parse_args()
    print("global_markets:", run_global_markets(args.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
