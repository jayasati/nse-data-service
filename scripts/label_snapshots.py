"""P8 — fill realised forward sector-excess labels on matured factor snapshots.

For each `factor_snapshot` row with a NULL fwd_excess_{30,60,90,120}, compute the
stock's forward return at that horizon minus its sector-ETF benchmark (NIFTYBEES
fallback) minus round-trip cost — the SAME excess definition the backtests use — and
write it back. A horizon is only filled once enough forward candles exist, so this is
safe to run daily: it tops up labels as time passes and is a no-op for unmatured rows.

    PYTHONPATH=src .venv/bin/python -u scripts/label_snapshots.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

HZ = (30, 60, 90, 120)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--cost", type=float, default=0.50, help="round-trip cost %% subtracted")
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import snapshot

    conn = open_db(args.db)
    filled = snapshot.label_matured(conn, cost=args.cost)
    if not any(filled.values()):
        print("no snapshots matured for labelling yet.")
    else:
        print("labelled: " + "  ".join(f"{h}d:+{filled[h]}" for h in HZ))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
