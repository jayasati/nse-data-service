"""Live ranked output of the validated composite (Quality+Value) — the usable
product. Ranks a grade tier by composite score as-of now, with explainable
Quality/Value drivers per name.

    PYTHONPATH=src .venv/bin/python -u scripts/rank_today.py --grade "B tradeable" --top 15
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--grade", default="B tradeable")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import composite_engine
    from nse_data.research.score import as_of_now_epoch
    from nse_data.fundamentals.sectors import sector_class_for

    conn = open_db(args.db)
    syms = [r[0] for r in conn.execute(
        "SELECT symbol FROM tradeable_universe WHERE grade=?", (args.grade,))]
    sec = {}
    def sector_of(s):
        if s not in sec:
            sec[s] = sector_class_for(s).value
        return sec[s]

    scored = composite_engine.score_universe(conn, syms, as_of_now_epoch(), sector_of)
    ranked = sorted(scored.items(), key=lambda kv: -kv[1]["score"])
    print(f"=== {args.grade}: composite (Quality+Value) ranking — {len(ranked)} scored ===")
    print(f"  {'#':>3} {'symbol':<12} {'comp':>5} {'qual':>5} {'val':>5}  sector")
    def row(rank, s, r):
        c = r["components"]
        print(f"  {rank:>3} {s:<12} {r['score']:>5.1f} {c.get('quality','-'):>5} "
              f"{c.get('valuation','-'):>5}  {sector_of(s)}")
    for i, (s, r) in enumerate(ranked[:args.top], 1):
        row(i, s, r)
    print("  ...")
    for i, (s, r) in enumerate(ranked[-5:], len(ranked) - 4):
        row(i, s, r)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
