"""Daily paper-trade / live-signal CLI. Thin wrapper over
`nse_data.research.paper_trade` — runs one or more strategies, each accumulating an
INDEPENDENT forward, out-of-sample track record in paper_book (tagged by `strategy`).

The service runs this automatically every trading day at 19:15 IST
(`register_paper_trade_job`); this CLI is for manual/ad-hoc runs and dry-runs.

    PYTHONPATH=src .venv/bin/python -u scripts/paper_trade.py                 # all strategies
    PYTHONPATH=src .venv/bin/python -u scripts/paper_trade.py --strategies lean
    PYTHONPATH=src .venv/bin/python -u scripts/paper_trade.py --dry-run       # report only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _print_strategy(today: str, params, s: dict) -> None:
    dry = "[DRY-RUN]" if params.dry_run else ""
    print(f"\n=== {s['label']} [{s['key']}]  as-of {today}  scored={s['scored']} {dry} ===")
    print(f"caps: opened {len(s['buys'])} of {s['eligible_buys']} eligible "
          f"(capped {s['capped']})  ·  heat {s['heat_used_pct']}% of cap")
    print("BUY ({}):  ".format(len(s["buys"]))
          + (", ".join(f"{sym}({sc:.0f})" for sym, sc in s["buys"][:20]) or "—"))
    print("SELL ({}): ".format(len(s["sells"]))
          + (", ".join(f"{sym} {net:+.1f}% [{r}]" for sym, net, r in s["sells"]) or "—"))
    print(f"HOLDINGS ({s['n_open']}):")
    for sym, ed, days, unr, ls, rtag in s["holdings"][:40]:
        print(f"  {sym:12s} in {ed} ({days:>3}d)  {unr:+6.1f}%  score={ls}{rtag}")
    if s["closed_n"]:
        print(f"CLOSED: {s['closed_n']} trades  win={s['win_pct']:.0f}%  "
              f"avg={s['avg_pct']:+.2f}%  total={s['total_pct']:+.1f}%")


def main() -> int:
    from nse_data.research.paper_trade import (
        STRATEGIES, PaperTradeParams, run_paper_trade,
    )
    from nse_data.storage.db import open_db, apply_migrations

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--strategies", default=",".join(STRATEGIES),
                    help="comma list: " + ",".join(STRATEGIES))
    ap.add_argument("--t-in", type=float, default=80.0)
    ap.add_argument("--t-out", type=float, default=60.0)
    ap.add_argument("--trail", type=float, default=15.0)
    ap.add_argument("--max-hold", type=int, default=120)
    ap.add_argument("--stop", type=float, default=-15.0)
    ap.add_argument("--cost", type=float, default=1.0)
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--risk-pct", type=float, default=1.0, help="%% of capital risked per trade")
    ap.add_argument("--atr-k", type=float, default=2.5, help="stop distance = k x ATR")
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--heat-pct", type=float, default=10.0, help="max total open risk %% of capital")
    ap.add_argument("--sector-max", type=int, default=3, help="max concurrent positions per sector")
    ap.add_argument("--trail-atr-k", type=float, default=3.0, help="chandelier trail = k x ATR")
    ap.add_argument("--trail-window", type=int, default=22, help="chandelier highest-close lookback")
    ap.add_argument("--dry-run", action="store_true", help="report signals, don't write the book")
    args = ap.parse_args()

    conn = open_db(args.db)
    conn.execute("PRAGMA busy_timeout=60000")
    apply_migrations(conn)

    params = PaperTradeParams(
        t_in=args.t_in, t_out=args.t_out, trail=args.trail, max_hold=args.max_hold,
        stop=args.stop, cost=args.cost, dry_run=args.dry_run,
        capital=args.capital, risk_pct=args.risk_pct, atr_k=args.atr_k,
        max_positions=args.max_positions, heat_pct=args.heat_pct, sector_max=args.sector_max,
        trail_atr_k=args.trail_atr_k, trail_window=args.trail_window,
    )
    keys = [s.strip() for s in args.strategies.split(",") if s.strip() in STRATEGIES]
    report = run_paper_trade(conn, strategies=keys, params=params)

    print(f"paper-trade as-of {report['today']}  eligible={report['eligible']}")
    for s in report["strategies"]:
        _print_strategy(report["today"], params, s)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
