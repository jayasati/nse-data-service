"""P4 labeler: fill net-of-cost forward returns for open paper_observations once
enough forward candles exist. Same return math as backtest_move_causes.py —
move-day close entry, benchmark-ETF excess (removes market/sector beta), traded in
the move's own direction (momentum), minus round-trip cost.

A row is marked 'labeled' only when the longest horizon (10d) is available;
shorter horizons are filled as soon as they elapse, so re-runs are idempotent and
progressively complete a row. Run nightly after the candle sync.

    PYTHONPATH=src .venv/bin/python -u scripts/label_paper_observations.py --cost-pct 0.20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

HORIZONS = (1, 3, 5, 10)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--cost-pct", type=float, default=0.20,
                    help="round-trip cost subtracted from net_* (matches P1)")
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.research import edge_stats

    conn = open_db(args.db)
    bench_for, series, avail = edge_stats.bench_resolver(conn)

    rows = conn.execute(
        "SELECT id, symbol, move_date, sector, direction, entry_price "
        "FROM paper_observations WHERE status='open' ORDER BY move_date"
    ).fetchall()

    n_full = n_partial = n_pending = 0
    for obs_id, symbol, date, sector, direction, entry in rows:
        if not entry:
            continue
        bars = conn.execute(
            "SELECT date(ts,'unixepoch','+05:30') d, close FROM raw_intraday_candles "
            "WHERE symbol=? AND interval='day' AND date(ts,'unixepoch','+05:30') >= ? "
            "AND close IS NOT NULL ORDER BY ts LIMIT ?",
            (symbol, date, max(HORIZONS) + 3),
        ).fetchall()
        if len(bars) < 2 or bars[0][0] != date:
            n_pending += 1
            continue

        bench = bench_for(sector or "")
        bser, bdates = series[bench]
        sign = 1.0 if direction == "up" else -1.0
        b0 = edge_stats.close_on_or_before(bser, bdates, date)

        sets, vals = [], []
        for h in HORIZONS:
            if h < len(bars) and bars[h][1]:
                exit_date, exit_close = bars[h]
                stock_ret = (exit_close / entry - 1.0) * 100.0
                b1 = edge_stats.close_on_or_before(bser, bdates, exit_date)
                bench_ret = (b1 / b0 - 1.0) * 100.0 if (b0 and b1) else 0.0
                excess = stock_ret - bench_ret
                net = excess * sign - args.cost_pct
                sets += [f"ret_{h}d=?", f"excess_{h}d=?", f"net_{h}d=?"]
                vals += [round(stock_ret, 3), round(excess, 3), round(net, 3)]

        if not sets:
            n_pending += 1
            continue

        complete = bars and len(bars) > max(HORIZONS) and bars[max(HORIZONS)][1]
        sets += ["bench_symbol=?", "cost_pct=?"]
        vals += [bench, args.cost_pct]
        if complete:
            sets += ["status='labeled'", "labeled_at=datetime('now')"]
            n_full += 1
        else:
            n_partial += 1
        conn.execute(f"UPDATE paper_observations SET {','.join(sets)} WHERE id=?",
                     (*vals, obs_id))
    conn.commit()

    labeled = conn.execute("SELECT COUNT(*) FROM paper_observations WHERE status='labeled'").fetchone()[0]
    print(f"labeler: fully labeled this run={n_full}  partial={n_partial}  "
          f"pending(not enough candles)={n_pending}")
    print(f"  paper_observations labeled total={labeled}  benches: "
          + ",".join(f"{b}={avail[b]}" for b in sorted(avail)))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
