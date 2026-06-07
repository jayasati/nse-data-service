"""Run the backtest across a universe of symbols and aggregate the results.

The engine is per-symbol; this module fans out, attaches `symbol` to each
trade, and rolls up aggregates. Persistence is delegated to
`persistence.write_run` when `commit=True`.

Strategy dispatch: step 3 introduces a registry so this module routes
to the right per-symbol engine based on `cfg.strategy`. Until then, only
bb_ema9_30m exists and is imported directly.

No threading in MVP — even ~600 symbols × ~5.5 months of 30-min bars runs in
under a minute on a typical laptop, and Python's GIL plus pandas internals
make naive thread pools a wash here. If we hit a wall, add multiprocessing.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Iterable

from .persistence import write_run
from .types import RunReport, StrategyConfig, SymbolSignal, SymbolTrade

LOG = logging.getLogger(__name__)


def run_backtest_for_universe(
    conn: sqlite3.Connection,
    symbols: Iterable[str],
    *,
    cfg: StrategyConfig,
    start_date: str | None = None,
    end_date: str | None = None,
    progress_every: int = 25,
) -> RunReport:
    """Fan out the engine across `symbols`. Pure compute — no DB writes.

    Dispatches to the right strategy engine via the strategies registry
    (lookup keyed on `cfg.strategy`). Logs progress every `progress_every`
    symbols (set 0 to disable).
    """
    # Local import to avoid a strategies → _core → strategies cycle.
    from ..strategies.registry import resolve

    run_backtest_for_symbol = resolve(cfg.strategy).engine_fn

    all_signals: list[SymbolSignal] = []
    all_trades: list[SymbolTrade] = []

    symbols = list(symbols)
    total = len(symbols)
    skipped = 0

    for i, symbol in enumerate(symbols, start=1):
        if progress_every and (i == 1 or i % progress_every == 0 or i == total):
            LOG.info(
                "  [%d/%d] %s — sigs so far: %d, trades: %d",
                i, total, symbol, len(all_signals), len(all_trades),
            )
        try:
            signals, trades = run_backtest_for_symbol(
                conn, symbol,
                start_date=start_date, end_date=end_date, cfg=cfg,
            )
        except Exception as e:
            LOG.warning("backtest failed for %s: %r", symbol, e)
            skipped += 1
            continue

        for s in signals:
            all_signals.append(SymbolSignal(
                symbol=symbol, direction=s.direction, setup_ts=s.setup_ts,
                rr=s.rr, armed=s.armed,
            ))
        for t in trades:
            raw = t.pnl_raw()
            lev = t.pnl_leveraged(cfg.leverage)
            net = t.pnl_net()
            all_trades.append(SymbolTrade(
                symbol=symbol,
                direction=t.direction,
                setup_ts=t.setup_ts, entry_ts=t.entry_ts, entry_price=t.entry_price,
                sl=t.sl, target=t.target,
                exit_ts=t.exit_ts, exit_price=t.exit_price, exit_reason=t.exit_reason,
                qty=t.qty, rr_at_entry=t.rr_at_entry,
                pnl_raw=raw, pnl_leveraged=lev, pnl_net=net,
                signal_tags=t.signal_tags,
            ))

    if skipped:
        LOG.warning("skipped %d/%d symbols due to errors", skipped, total)

    return RunReport(signals=all_signals, trades=all_trades)


def commit_report(
    conn: sqlite3.Connection,
    report: RunReport,
    *,
    cfg: StrategyConfig,
    universe: str,
    symbols_count: int,
    start_date: str,
    end_date: str,
    notes: str | None = None,
) -> int:
    """Persist a RunReport to the DB. Returns the new run_id."""
    return write_run(
        conn,
        cfg=cfg,
        universe=universe,
        symbols_count=symbols_count,
        start_date=start_date,
        end_date=end_date,
        trades=report.trades,
        total_signals=report.total_signals,
        notes=notes,
    )
