"""Print the liquidity-gated Lean-score ranking (Valuation+Surprise, OOS-validated) —
the tradeable top-N. Illiquid micro-caps are excluded via tradeable_universe grades.

    PYTHONPATH=src .venv/bin/python scripts/lean_rank.py --limit 25
    PYTHONPATH=src .venv/bin/python scripts/lean_rank.py --grades "A core" --limit 30
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--grades", default="A core,B tradeable",
                    help="comma list of tradeable_universe grades, or 'all' to disable the gate")
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import lean_rank as lr
    conn = open_db(args.db)
    grades = () if args.grades.strip().lower() == "all" else tuple(g.strip() for g in args.grades.split(","))
    rows = lr.rank(conn, grades=grades or lr.TRADEABLE, limit=args.limit)
    conn.close()
    if not rows:
        print("no rows — is factor_snapshot / tradeable_universe populated?")
        return 1
    gate = "ALL (no liquidity gate)" if not grades else "+".join(grades)
    print(f"\nLEAN RANKING (Valuation+Surprise · OOS-validated) — gate: {gate}\n")
    print(f"  {'#':>2} {'symbol':<12}{'sector':<10}{'grade':<12}{'lean':>5} {'val':>4} {'surp':>5} "
          f"{'turnCr':>7} {'risk':>5}")
    for i, r in enumerate(rows, 1):
        sf = f"{r['surprise']:.0f}" if r.get("surprise") is not None else "—"
        tv = f"{r['turnover_cr']:.0f}" if r.get("turnover_cr") is not None else "—"
        rk = f"{r['risk']:.0f}" if r.get("risk") is not None else "—"
        star = "*" if r.get("both_factors") else " "
        print(f"  {i:>2} {r['symbol']:<12}{(r['sector'] or '')[:9]:<10}{(r['grade'] or '')[:11]:<12}"
              f"{r['lean_score']:>5.1f} {r['valuation']:>4.0f} {sf:>5} {tv:>7} {rk:>5}{star}")
    print("\n  (* = both Valuation AND Surprise present — strongest signal; "
          "others scored on Valuation alone)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
