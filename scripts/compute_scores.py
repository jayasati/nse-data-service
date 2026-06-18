"""Compute + persist the per-stock conviction score (a daily snapshot) for the
universe. Transparent summary now; history accumulates for forward validation.

    PYTHONPATH=src .venv/bin/python -u scripts/compute_scores.py            # universe
    PYTHONPATH=src .venv/bin/python -u scripts/compute_scores.py --symbols MTARTECH --show
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GRADES = ("A core", "B tradeable", "C volatile")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--symbols", help="comma list (default: tracked universe)")
    ap.add_argument("--grades", default=",".join(GRADES))
    ap.add_argument("--show", action="store_true", help="print each score + breakdown")
    args = ap.parse_args()

    from nse_data.storage.db import open_db, apply_migrations
    from nse_data.research.score import compute_score, as_of_now_epoch

    conn = open_db(args.db)
    conn.execute("PRAGMA busy_timeout=60000")
    apply_migrations(conn)

    grades = tuple(g.strip() for g in args.grades.split(",") if g.strip())
    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        ph = ",".join("?" * len(grades))
        syms = [r[0] for r in conn.execute(
            f"SELECT symbol FROM tradeable_universe WHERE grade IN ({ph})", grades)]

    as_of = as_of_now_epoch()
    day = dt.datetime.fromtimestamp(as_of, dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()
    n = 0
    for sym in syms:
        r = compute_score(conn, sym, as_of)
        conn.execute(
            "INSERT OR REPLACE INTO stock_score (symbol, computed_date, score, "
            "components_json, computed_epoch) VALUES (?,?,?,?,?)",
            (sym, day, r["score"], json.dumps(r["components"]), as_of))
        n += 1
        if args.show:
            comps = "  ".join(f"{k}{v:+.3f}" for k, v in r["components"].items()) or "(neutral)"
            print(f"  {sym:<12} score={r['score']:.3f}   {comps}")
        if n % 100 == 0:
            conn.commit()
    conn.commit()

    top = conn.execute(
        "SELECT symbol, score FROM stock_score WHERE computed_date=? ORDER BY score DESC LIMIT 8",
        (day,)).fetchall()
    bot = conn.execute(
        "SELECT symbol, score FROM stock_score WHERE computed_date=? ORDER BY score ASC LIMIT 8",
        (day,)).fetchall()
    print(f"\nscored {n} symbols for {day}")
    print("  top:", ", ".join(f"{s}={sc:.2f}" for s, sc in top))
    print("  bottom:", ", ".join(f"{s}={sc:.2f}" for s, sc in bot))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
