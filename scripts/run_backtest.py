"""Run the BB+EMA9 30-min backtester against the existing SQLite DB.

    # Dry-run on a few names — prints aggregates, no DB writes
    PYTHONPATH=src python scripts/run_backtest.py --symbols RELIANCE,TCS

    # Full FNO + Nifty 500 universe, default date range, persist results
    PYTHONPATH=src python scripts/run_backtest.py --commit --notes "first cut"

    # Different leverage / gap settings
    PYTHONPATH=src python scripts/run_backtest.py --leverage 4.0 --gap-mode overnight

`--commit` is OFF by default so iterating on parameters doesn't litter the DB.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nse_data.backtester.config import BacktestConfig  # noqa: E402
from nse_data.backtester.runner import (                # noqa: E402
    RunReport, commit_report, run_backtest_for_universe,
)
from nse_data.indicators.universe import fno_plus_nifty500  # noqa: E402

LOG = logging.getLogger("backtest_cli")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="data/nse.db")
    p.add_argument("--symbols", help="comma-separated; omit -> FNO+Nifty500")
    p.add_argument("--start", help="IST trading date YYYY-MM-DD (default: earliest available)")
    p.add_argument("--end", help="IST trading date YYYY-MM-DD (default: latest available)")
    p.add_argument("--leverage", type=float, default=5.0)
    p.add_argument("--rr-min", type=float, default=1.5)
    p.add_argument("--gap-pct", type=float, default=0.003)
    p.add_argument("--gap-mode", choices=["any", "overnight"], default="any")
    p.add_argument("--allow-reentry", action="store_true",
                   help="Allow multiple trades per symbol per session")
    p.add_argument("--commit", action="store_true",
                   help="Persist run + trades to DB (off by default)")
    p.add_argument("--notes", default=None)
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def _resolve_symbols(conn: sqlite3.Connection, raw: str | None) -> list[str]:
    if raw:
        return [s.strip().upper() for s in raw.split(",") if s.strip()]
    universe = fno_plus_nifty500(conn)
    if not universe:
        LOG.warning("FNO+N500 universe is empty; falling back to all symbols "
                    "in raw_intraday_candles")
        universe = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM raw_intraday_candles ORDER BY symbol"
        )]
    return universe


def _print_summary(report: RunReport, cfg: BacktestConfig,
                   start: str, end: str, n_symbols: int) -> None:
    decided = report.wins + report.losses
    print("=" * 60)
    print(f"  BB+EMA9 30m backtest")
    print(f"  Universe   : {n_symbols} symbols")
    print(f"  Period     : {start} to {end}")
    print(f"  Leverage   : {cfg.leverage}x   Gap mode: {cfg.gap_mode}   RR>={cfg.rr_min}")
    print("-" * 60)
    print(f"  Signals    : {report.total_signals}")
    print(f"  Trades     : {report.total_trades}")
    print(f"  Wins       : {report.wins}   Losses: {report.losses}")
    if decided:
        print(f"  Win rate   : {report.win_rate * 100:.1f}%")
    print(f"  P&L raw    : {report.pnl_raw:,.2f}")
    print(f"  P&L lev    : {report.pnl_leveraged:,.2f}  ({cfg.leverage}x)")
    print("=" * 60)


def _print_top_movers(report: RunReport, n: int = 10) -> None:
    if not report.trades:
        return
    by_symbol: dict[str, float] = {}
    for t in report.trades:
        by_symbol[t.symbol] = by_symbol.get(t.symbol, 0.0) + t.pnl_leveraged
    ranked = sorted(by_symbol.items(), key=lambda kv: kv[1], reverse=True)
    print(f"\nTop {min(n, len(ranked))} winners:")
    for s, p in ranked[:n]:
        print(f"  {s:<15} {p:>+12,.2f}")
    if len(ranked) > n:
        print(f"\nBottom {min(n, len(ranked))} losers:")
        for s, p in ranked[-n:][::-1]:
            print(f"  {s:<15} {p:>+12,.2f}")


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = BacktestConfig(
        leverage=args.leverage,
        rr_min=args.rr_min,
        gap_pct=args.gap_pct,
        gap_mode=args.gap_mode,
        allow_reentry=args.allow_reentry,
    )

    conn = sqlite3.connect(args.db)
    try:
        symbols = _resolve_symbols(conn, args.symbols)
        if not symbols:
            print("No symbols to run on.", file=sys.stderr)
            return 1

        start = args.start or _earliest_date(conn)
        end   = args.end   or _latest_date(conn)

        LOG.info("starting backtest: %d symbols, %s..%s, gap_mode=%s, lev=%s",
                 len(symbols), start, end, cfg.gap_mode, cfg.leverage)
        t0 = time.time()
        report = run_backtest_for_universe(
            conn, symbols, cfg=cfg, start_date=start, end_date=end,
        )
        LOG.info("done in %.1fs", time.time() - t0)

        _print_summary(report, cfg, start, end, len(symbols))
        _print_top_movers(report)

        if args.commit:
            run_id = commit_report(
                conn, report,
                cfg=cfg,
                universe="custom" if args.symbols else "fno_plus_nifty500",
                symbols_count=len(symbols),
                start_date=start, end_date=end, notes=args.notes,
            )
            print(f"\nPersisted as backtest_runs.id = {run_id}")
        else:
            print("\n(dry-run; pass --commit to persist)")
        return 0
    finally:
        conn.close()


def _earliest_date(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MIN(date(ts,'unixepoch','+5 hours','+30 minutes')) "
        "FROM raw_intraday_candles WHERE interval='minute'"
    ).fetchone()
    return row[0] or "2025-01-01"


def _latest_date(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(date(ts,'unixepoch','+5 hours','+30 minutes')) "
        "FROM raw_intraday_candles WHERE interval='minute'"
    ).fetchone()
    return row[0] or "2025-12-31"


if __name__ == "__main__":
    raise SystemExit(main())
