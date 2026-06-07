"""Run a backtest against the existing SQLite DB.

    # Strategy #1 (BB+EMA9 30-min intraday) — dry-run on 5 names
    PYTHONPATH=src python scripts/run_backtest.py --strategy bb_ema9_30m \
        --symbols RELIANCE,TCS,HDFCBANK,SBIN,INFY

    # Strategy #2 (Williams %R + MACD daily) — full FNO+Nifty500, persist
    PYTHONPATH=src python scripts/run_backtest.py --strategy macd_willr_daily \
        --commit --notes "first daily run"

`--commit` is OFF by default so iterating on parameters doesn't litter the DB.

Per-strategy flags are namespaced:
  --bb-gap-pct  --bb-gap-mode                       (bb_ema9_30m)
  --md-rr-target  --md-gap-fill  --md-no-divergence (macd_willr_daily)
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nse_data.backtester._core.runner import (          # noqa: E402
    commit_report, run_backtest_for_universe,
)
from nse_data.backtester._core.types import RunReport, StrategyConfig  # noqa: E402
from nse_data.backtester.strategies.bb_ema9_30m.config import (  # noqa: E402
    BacktestConfig,
)
from nse_data.backtester.strategies.macd_willr_daily.config import (  # noqa: E402
    MacdWillrDailyConfig,
)
from nse_data.backtester.strategies.breakout_52wh.config import (  # noqa: E402
    Breakout52whConfig,
)
from nse_data.indicators.universe import fno_plus_nifty500  # noqa: E402

LOG = logging.getLogger("backtest_cli")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--strategy",
                   choices=["bb_ema9_30m", "macd_willr_daily", "breakout_52wh"],
                   default="bb_ema9_30m",
                   help="Which strategy to run (default: bb_ema9_30m).")

    # Common flags
    p.add_argument("--db", default="data/nse.db")
    p.add_argument("--symbols", help="comma-separated; omit -> FNO+Nifty500")
    p.add_argument("--start", help="trading date YYYY-MM-DD (default: earliest available)")
    p.add_argument("--end", help="trading date YYYY-MM-DD (default: latest available)")
    p.add_argument("--leverage", type=float, default=None,
                   help="P&L multiplier (default: strategy default — 5× intraday, 1× daily)")
    p.add_argument("--rr-min", type=float, default=None,
                   help="skip setups below this R:R (default: strategy default)")
    p.add_argument("--notional", type=float, default=100_000.0,
                   help="rupee notional per trade — qty = floor(notional/entry_price). "
                        "Default ₹1,00,000. Set 0 to keep qty=1 (debug only).")
    p.add_argument("--allow-reentry", action="store_true")
    p.add_argument("--commit", action="store_true",
                   help="Persist run + trades to DB (off by default)")
    p.add_argument("--notes", default=None)
    p.add_argument("--verbose", "-v", action="store_true")

    # bb_ema9_30m flags (prefixed --bb-)
    p.add_argument("--bb-gap-pct", type=float, default=0.003)
    p.add_argument("--bb-gap-mode", choices=["any", "overnight"], default="any")

    # macd_willr_daily flags (prefixed --md-)
    p.add_argument("--md-rr-target", type=float, default=2.0)
    p.add_argument("--md-pivot-window", type=int, default=5)
    p.add_argument("--md-divergence-lookback", type=int, default=60)
    p.add_argument("--md-no-divergence", action="store_true",
                   help="Disable bullish/bearish divergence path")
    p.add_argument("--md-gap-fill", choices=["open", "sl"], default="open")
    p.add_argument("--md-willr-hook-lookback", type=int, default=3)
    p.add_argument("--md-swing-lookback", type=int, default=10)

    # breakout_52wh flags (prefixed --bo-)
    p.add_argument("--bo-vol-ratio", type=float, default=1.5,
                   help="min volume / 20d-avg to confirm the breakout")
    p.add_argument("--bo-atr-mult", type=float, default=1.5,
                   help="ATR multiple for the SL/T1 bracket")
    p.add_argument("--bo-max-hold", type=int, default=5,
                   help="exit at close after N bars if neither level hit")
    p.add_argument("--bo-lookback", type=int, default=252,
                   help="trading-day window for the 52-week high")

    return p.parse_args()


def _build_config(args: argparse.Namespace) -> StrategyConfig:
    """Construct the right config for the chosen strategy. Warn if flags from
    the OTHER strategy were touched (mistake-friendly, not an error)."""
    if args.strategy == "bb_ema9_30m":
        if any([args.md_rr_target != 2.0, args.md_pivot_window != 5,
                args.md_divergence_lookback != 60, args.md_no_divergence,
                args.md_gap_fill != "open", args.md_willr_hook_lookback != 3,
                args.md_swing_lookback != 10]):
            LOG.warning("--md-* flags ignored for strategy bb_ema9_30m")
        return BacktestConfig(
            leverage=args.leverage if args.leverage is not None else 5.0,
            rr_min=args.rr_min if args.rr_min is not None else 1.5,
            notional_per_trade=args.notional,
            gap_pct=args.bb_gap_pct,
            gap_mode=args.bb_gap_mode,
            allow_reentry=args.allow_reentry,
        )

    if args.strategy == "breakout_52wh":
        return Breakout52whConfig(
            leverage=args.leverage if args.leverage is not None else 1.0,
            rr_min=args.rr_min if args.rr_min is not None else 0.0,
            notional_per_trade=args.notional,
            vol_ratio_min=args.bo_vol_ratio,
            atr_mult=args.bo_atr_mult,
            max_hold_days=args.bo_max_hold,
            lookback_52w=args.bo_lookback,
            allow_reentry=args.allow_reentry,
        )

    # macd_willr_daily
    if args.bb_gap_pct != 0.003 or args.bb_gap_mode != "any":
        LOG.warning("--bb-* flags ignored for strategy macd_willr_daily")
    return MacdWillrDailyConfig(
        leverage=args.leverage if args.leverage is not None else 1.0,
        rr_min=args.rr_min if args.rr_min is not None else 0.0,
        notional_per_trade=args.notional,
        rr_target=args.md_rr_target,
        pivot_window=args.md_pivot_window,
        divergence_lookback=args.md_divergence_lookback,
        use_divergence=not args.md_no_divergence,
        gap_fill=args.md_gap_fill,
        willr_hook_lookback=args.md_willr_hook_lookback,
        swing_lookback=args.md_swing_lookback,
        allow_reentry=args.allow_reentry,
    )


def _resolve_symbols(conn: sqlite3.Connection, raw: str | None,
                     strategy: str) -> list[str]:
    if raw:
        return [s.strip().upper() for s in raw.split(",") if s.strip()]
    universe = fno_plus_nifty500(conn)
    if not universe:
        LOG.warning("FNO+N500 universe is empty; falling back to "
                    "%s table",
                    "raw_intraday_candles" if strategy == "bb_ema9_30m" else "raw_bhavcopy_cm")
        if strategy == "bb_ema9_30m":
            universe = [r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM raw_intraday_candles ORDER BY symbol"
            )]
        else:
            universe = [r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM raw_bhavcopy_cm WHERE series='EQ' ORDER BY symbol"
            )]
    return universe


def _earliest_date(conn: sqlite3.Connection, strategy: str) -> str:
    if strategy == "bb_ema9_30m":
        row = conn.execute(
            "SELECT MIN(date(ts,'unixepoch','+5 hours','+30 minutes')) "
            "FROM raw_intraday_candles WHERE interval='minute'"
        ).fetchone()
        return row[0] or "2025-01-01"
    row = conn.execute(
        "SELECT MIN(date) FROM raw_bhavcopy_cm WHERE series='EQ'"
    ).fetchone()
    return row[0] or "2024-01-01"


def _latest_date(conn: sqlite3.Connection, strategy: str) -> str:
    if strategy == "bb_ema9_30m":
        row = conn.execute(
            "SELECT MAX(date(ts,'unixepoch','+5 hours','+30 minutes')) "
            "FROM raw_intraday_candles WHERE interval='minute'"
        ).fetchone()
        return row[0] or "2025-12-31"
    row = conn.execute(
        "SELECT MAX(date) FROM raw_bhavcopy_cm WHERE series='EQ'"
    ).fetchone()
    return row[0] or "2025-12-31"


def _print_summary(report: RunReport, cfg: StrategyConfig,
                   start: str, end: str, n_symbols: int) -> None:
    decided = report.wins + report.losses
    print("=" * 60)
    print(f"  Backtest — strategy {cfg.strategy}")
    print(f"  Universe   : {n_symbols} symbols")
    print(f"  Period     : {start} to {end}")
    print(f"  Leverage   : {cfg.leverage}x   RR_min: {cfg.rr_min}")
    print(f"  Notional   : ₹{cfg.notional_per_trade:,.0f} / trade "
          f"(qty = floor(notional / entry_price))")
    print("-" * 60)
    print(f"  Signals    : {report.total_signals}")
    print(f"  Trades     : {report.total_trades}")
    print(f"  Wins       : {report.wins}   Losses: {report.losses}")
    if decided:
        print(f"  Win rate   : {report.win_rate * 100:.1f}%")
    print(f"  P&L raw    : {report.pnl_raw:,.2f}")
    print(f"  P&L lev    : {report.pnl_leveraged:,.2f}  ({cfg.leverage}x)")

    # Signal-tag breakdown for strategies that classify their setups
    tags: dict[str, int] = {}
    for t in report.trades:
        key = t.signal_tags or "untagged"
        tags[key] = tags.get(key, 0) + 1
    if tags and not (len(tags) == 1 and "untagged" in tags):
        print(f"  By tag     : {', '.join(f'{k}={v}' for k, v in sorted(tags.items()))}")
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

    cfg = _build_config(args)

    conn = sqlite3.connect(args.db)
    try:
        symbols = _resolve_symbols(conn, args.symbols, args.strategy)
        if not symbols:
            print("No symbols to run on.", file=sys.stderr)
            return 1

        start = args.start or _earliest_date(conn, args.strategy)
        end   = args.end   or _latest_date(conn, args.strategy)

        LOG.info("starting backtest: strategy=%s, %d symbols, %s..%s, lev=%s",
                 cfg.strategy, len(symbols), start, end, cfg.leverage)
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


if __name__ == "__main__":
    raise SystemExit(main())
