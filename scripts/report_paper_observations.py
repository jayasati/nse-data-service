"""P4 report / promote gate: per-catalyst net-of-cost drift verdict from the
paper log. THE GATE READS batch='forward' ONLY — the honest out-of-sample stream.
batch='backfill' is reported separately as colour and must never gate a promote.

A catalyst category PROMOTES (becomes P3-eligible, live Telegram) only when its
forward-batch net CI excludes 0 positive at n >= min-n. Record the promote as a
dated row in PLAN.md (Standing Rule 1: validation is law). See
plans/P4_PAPER_TRADE_PLAN.md.

    PYTHONPATH=src .venv/bin/python -u scripts/report_paper_observations.py \
        --horizon 3 --min-n 30
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

HORIZONS = (1, 3, 5, 10)


def _verdict_table(conn, edge_stats, batch, horizon, min_n, bootstrap):
    col = f"net_{horizon}d"
    rows = conn.execute(
        f"SELECT catalyst, {col} FROM paper_observations "
        f"WHERE batch=? AND status='labeled' AND {col} IS NOT NULL",
        (batch,),
    ).fetchall()
    cells = defaultdict(list)
    for catalyst, net in rows:
        cells[catalyst or "none"].append(net)
        cells["ALL"].append(net)

    print(f"\n=== {batch.upper()} — forward-drift verdict @ {horizon}d "
          f"(net-of-cost excess; CI excl 0 ⇒ TRADE) ===")
    if not rows:
        print("  (no labeled rows in this batch yet)")
        return
    print(f"{'catalyst':<16}{'n':>6}{'net%':>8}{'hit%':>6}{'95% CI':>18}  verdict")
    print("-" * 62)
    order = sorted(cells, key=lambda k: (k != "ALL", -len(cells[k])))
    for cat in order:
        nets = cells[cat]
        mean = sum(nets) / len(nets)
        hit = sum(1 for r in nets if r > 0) / len(nets) * 100
        ci = edge_stats.bootstrap_ci(nets, bootstrap)
        verd = edge_stats.verdict(len(nets), ci, min_n)
        ci_txt = f"[{ci[0]:+.2f},{ci[1]:+.2f}]" if ci else "—"
        promote = "  <-- PROMOTE" if (batch == "forward" and verd == "TRADE" and cat != "ALL") else ""
        print(f"{cat:<16}{len(nets):>6}{mean:>7.2f}%{hit:>5.0f}%{ci_txt:>18}  {verd}{promote}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--horizon", type=int, default=3, choices=HORIZONS)
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--bootstrap", type=int, default=10000)
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import edge_stats

    conn = open_db(args.db)

    # capture census
    print("=== PAPER OBSERVATIONS — capture census ===")
    for batch, in conn.execute("SELECT DISTINCT batch FROM paper_observations ORDER BY batch"):
        tot, lab = conn.execute(
            "SELECT COUNT(*), SUM(status='labeled') FROM paper_observations WHERE batch=?",
            (batch,)).fetchone()
        rng = conn.execute(
            "SELECT MIN(move_date), MAX(move_date) FROM paper_observations WHERE batch=?",
            (batch,)).fetchone()
        print(f"  {batch:<9} captured={tot:<6} labeled={lab or 0:<6} dates {rng[0]}..{rng[1]}")

    # the gate (forward) + colour (backfill)
    _verdict_table(conn, edge_stats, "forward", args.horizon, args.min_n, args.bootstrap)
    _verdict_table(conn, edge_stats, "backfill", args.horizon, args.min_n, args.bootstrap)

    print("\n(GATE = forward batch only. A catalyst PROMOTES to P3 when its forward "
          "net CI excludes 0 positive at n>=min-n → record a dated row in PLAN.md.)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
