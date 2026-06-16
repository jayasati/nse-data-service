"""What drives big single-day moves — and does the move pay AFTER the catalyst?

The edge search beyond earnings (user thesis: a stock shoots up 15% for many
reasons — orders, block deals, M&A, upgrades, regulatory…). For every big mover
in `intraday_move_events` (|move| ≥ threshold, on a tracked liquid name) we:

  1. CENSUS — attribute the FREE internal cause candidates (cause_engine.
     gather_candidates: announcements/orders, block & bulk deals, rating &
     brokerage actions, corp actions, OI spurts, 52w breaks, delivery spikes,
     F&O expiry…) and bucket the move under its highest-weight PRIMARY cause.
  2. DRIFT — measure the net-of-cost forward return FROM THE MOVE-DAY CLOSE, in
     the move's own direction (momentum), by cause type. Benchmark-ETF excess
     (removes market/sector beta) minus round-trip cost, 95% bootstrap CI, min-n
     gate (edge_stats). A cause is a TRADE (momentum/drift edge) only if the CI
     excludes 0 positive; significantly negative ⇒ AVOID = the pop FADES (a short).

No LLM / no news fetch — internal connectors only, so it runs over thousands of
moves for free. Run on EC2 for the full event history.

    PYTHONPATH=src .venv/bin/python -u scripts/backtest_move_causes.py
    ... --min-move 10 --since 2023-06-13 --cost-pct 0.20 --min-n 30
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TRACKED = ("A core", "B tradeable", "C volatile")

# Context flags, NOT catalysts: F&O expiry / a market-wide move co-occur with a
# big move by calendar/beta coincidence (the ±window catches monthly expiry for
# ~half of all moves). They must never win "primary cause" — exclude them so the
# real catalyst (an order, a block deal, an upgrade) surfaces; a move whose only
# tags are context is effectively 'none' (no idiosyncratic catalyst found).
_CONTEXT_ONLY = {"fno_expiry", "market_context"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--min-move", type=float, default=10.0,
                    help="abs %% move-from-open threshold for a 'big' mover")
    ap.add_argument("--since", default="2023-06-13")
    ap.add_argument("--horizons", default="1,2,3,5,10")
    ap.add_argument("--primary-horizon", type=int, default=3)
    ap.add_argument("--cost-pct", type=float, default=0.20)
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--bootstrap", type=int, default=10000)
    ap.add_argument("--window-days", type=int, default=4,
                    help="cause lookback window around the move (cause_engine)")
    ap.add_argument("--include-untracked", action="store_true",
                    help="also score illiquid/etf/ungraded names (default: tracked only)")
    args = ap.parse_args()
    horizons = [int(x) for x in args.horizons.split(",")]
    ph = args.primary_horizon

    from nse_data.storage.db import open_db
    from nse_data.research import cause_engine, edge_stats
    from nse_data.fundamentals.sectors import sector_class_for
    from nse_data.fundamentals.from_results import is_result_subject

    conn = open_db(args.db)
    bench_for, series, avail = edge_stats.bench_resolver(conn)

    # Big movers on tracked names, in window. LEFT JOIN universe so we can filter
    # to tracked grades (or include all with --include-untracked).
    rows = conn.execute(
        "SELECT e.symbol, e.date, e.direction, "
        "       COALESCE(t.grade,'(ungraded)') grade "
        "FROM intraday_move_events e LEFT JOIN tradeable_universe t ON t.symbol=e.symbol "
        "WHERE ABS(e.move_pct) >= ? AND e.date >= ? "
        "ORDER BY e.date",
        (args.min_move, args.since),
    ).fetchall()

    # cause type -> horizon -> [net excess returns, direction = move direction]
    cells: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    census = defaultdict(int)            # primary cause -> count
    census_dir = defaultdict(lambda: [0, 0])   # primary cause -> [up, down]
    n_total = n_scored = n_tracked = 0

    for symbol, date, direction, grade in rows:
        n_total += 1
        if not args.include_untracked and grade not in TRACKED:
            continue
        n_tracked += 1

        # forward bars from the move day (entry = move-day CLOSE → follow-through)
        bars = conn.execute(
            "SELECT date(ts,'unixepoch','+05:30') d, close FROM raw_intraday_candles "
            "WHERE symbol=? AND interval='day' AND date(ts,'unixepoch','+05:30') >= ? "
            "AND close IS NOT NULL ORDER BY ts LIMIT ?",
            (symbol, date, max(horizons) + 3),
        ).fetchall()
        if len(bars) < 2 or bars[0][0] != date or not bars[0][1]:
            continue
        entry = bars[0][1]
        fwd = {}
        for h in horizons:
            if h < len(bars) and bars[h][1]:
                fwd[h] = (bars[h][0], (bars[h][1] / entry - 1.0) * 100.0)
        if not fwd:
            continue

        # primary internal cause = highest-weight CATALYST (context flags excluded);
        # 'none' if no real catalyst was attributed.
        cands = cause_engine.gather_candidates(conn, symbol, date, window_days=args.window_days)
        catalysts = [c for c in cands if c["cause_type"] not in _CONTEXT_ONLY]
        if not catalysts:
            primary = "none"
        else:
            top = max(catalysts, key=lambda c: c["weight"])
            # split the catch-all 'announcement' into earnings-result vs other
            # (orders/contracts/deals) — the user's thesis is the NON-earnings half.
            if top["cause_type"] == "announcement":
                primary = "ann:result" if is_result_subject(top["summary"]) else "ann:other"
            else:
                primary = top["cause_type"]
        census[primary] += 1
        census_dir[primary][0 if direction == "up" else 1] += 1
        n_scored += 1

        sector = sector_class_for(symbol).value
        bser, bdates = series[bench_for(sector)]
        sign = 1.0 if direction == "up" else -1.0     # momentum: trade the move's way
        b0 = edge_stats.close_on_or_before(bser, bdates, date)
        for h, (exit_date, stock_ret) in fwd.items():
            b1 = edge_stats.close_on_or_before(bser, bdates, exit_date)
            bench_ret = (b1 / b0 - 1.0) * 100.0 if (b0 and b1) else 0.0
            net = (stock_ret - bench_ret) * sign - args.cost_pct
            cells[primary][h].append(net)
            cells["ALL"][h].append(net)

    conn.close()

    print(f"\n=== BIG-MOVE CAUSE CENSUS + DRIFT (|move|≥{args.min_move}%) ===")
    print(f"window≥{args.since} tracked_only={not args.include_untracked} "
          f"cost={args.cost_pct}%/trade min_n={args.min_n} primary_h={ph}d")
    print(f"big movers in window={n_total} scored={n_scored} "
          f"(tracked={n_tracked}); benches: " + ",".join(f"{b}={avail[b]}" for b in sorted(avail)))

    # ---- census: what drives big moves (primary internal cause) ----
    print(f"\n{'cause':<16}{'n':>6}{'up':>6}{'down':>6}{'%share':>8}")
    print("-" * 42)
    tot = sum(census.values()) or 1
    for c in sorted(census, key=lambda k: -census[k]):
        u, d = census_dir[c]
        print(f"{c:<16}{census[c]:>6}{u:>6}{d:>6}{100*census[c]/tot:>7.1f}%")

    # ---- drift verdict per cause type, at the fixed primary horizon ----
    print(f"\n=== FORWARD-DRIFT VERDICT @ {ph}d (momentum; net-of-cost excess; "
          f"CI excl 0 ⇒ TRADE, neg ⇒ pop FADES) ===")
    print(f"{'cause':<16}{'n':>6}{'net%':>8}{'hit%':>6}{'95% CI':>18}  verdict")
    print("-" * 62)
    order = sorted(cells, key=lambda k: (k != "ALL", -len(cells[k].get(ph, []))))
    for cause in order:
        rets = cells[cause].get(ph, [])
        if not rets:
            continue
        mean = sum(rets) / len(rets)
        hit = sum(1 for r in rets if r > 0) / len(rets) * 100
        ci = edge_stats.bootstrap_ci(rets, args.bootstrap)
        verd = edge_stats.verdict(len(rets), ci, args.min_n)
        ci_txt = f"[{ci[0]:+.2f},{ci[1]:+.2f}]" if ci else "—"
        print(f"{cause:<16}{len(rets):>6}{mean:>7.2f}%{hit:>5.0f}%{ci_txt:>18}  {verd}")

    # ---- horizon structure for the top causes ----
    print(f"\n{'cause':<16}{'n':>6} " + "".join(f"{'net@'+str(h):>9}" for h in horizons))
    print("-" * (23 + 9 * len(horizons)))
    for cause in order:
        n0 = len(cells[cause].get(horizons[0], []))
        if n0 < args.min_n and cause != "ALL":
            continue
        line = f"{cause:<16}{n0:>6} "
        for h in horizons:
            r = cells[cause].get(h, [])
            line += f"{(sum(r)/len(r)):>8.2f}%" if r else f"{'—':>9}"
        print(line)
    print("\n(net@h = move-direction EXCESS return over the benchmark ETF minus "
          f"{args.cost_pct}% cost, h sessions after the move-day close. Positive = "
          "the catalyst move keeps drifting; negative = it fades/reverts.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
