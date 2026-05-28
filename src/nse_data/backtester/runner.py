"""Run the backtest across a universe of symbols and aggregate the results.

The engine is per-symbol; this module fans out, attaches `symbol` to each
trade, and rolls up aggregates. Persistence is delegated to
`persistence.write_run` when `commit=True`.

No threading in MVP — even ~600 symbols × ~5.5 months of 30-min bars runs in
under a minute on a typical laptop, and Python's GIL plus pandas internals
make naive thread pools a wash here. If we hit a wall, add multiprocessing.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Iterable

from .config import BacktestConfig
from .engine import run_backtest_for_symbol
from .persistence import write_run

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SymbolTrade:
    """A Trade with its symbol attached, plus pre-computed pnl fields so the
    persistence layer doesn't have to call methods or know about leverage."""
    symbol: str
    direction: str
    setup_ts: int
    entry_ts: int
    entry_price: float
    sl: float
    target: float
    exit_ts: int
    exit_price: float
    exit_reason: str
    qty: int
    rr_at_entry: float
    pnl_raw: float
    pnl_leveraged: float


@dataclass(frozen=True)
class SymbolSignal:
    symbol: str
    direction: str
    setup_ts: int
    rr: float
    armed: bool


@dataclass(frozen=True)
class RunReport:
    signals: list[SymbolSignal]
    trades: list[SymbolTrade]

    @property
    def total_signals(self) -> int:
        return len(self.signals)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl_raw > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.pnl_raw < 0)

    @property
    def pnl_raw(self) -> float:
        return sum(t.pnl_raw for t in self.trades)

    @property
    def pnl_leveraged(self) -> float:
        return sum(t.pnl_leveraged for t in self.trades)

    @property
    def win_rate(self) -> float:
        decided = self.wins + self.losses
        return self.wins / decided if decided > 0 else 0.0


def run_backtest_for_universe(
    conn: sqlite3.Connection,
    symbols: Iterable[str],
    *,
    cfg: BacktestConfig,
    start_date: str | None = None,
    end_date: str | None = None,
    progress_every: int = 25,
) -> RunReport:
    """Fan out the engine across `symbols`. Pure compute — no DB writes.

    Logs a progress line every `progress_every` symbols so long full-universe
    runs aren't silent. Set progress_every=0 to disable.
    """
    all_signals: list[SymbolSignal] = []
    all_trades: list[SymbolTrade] = []

    symbols = list(symbols)
    total = len(symbols)

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
            continue

        for s in signals:
            all_signals.append(SymbolSignal(
                symbol=symbol, direction=s.direction, setup_ts=s.setup_ts,
                rr=s.rr, armed=s.armed,
            ))
        for t in trades:
            raw = t.pnl_raw()
            lev = t.pnl_leveraged(cfg.leverage)
            all_trades.append(SymbolTrade(
                symbol=symbol,
                direction=t.direction,
                setup_ts=t.setup_ts, entry_ts=t.entry_ts, entry_price=t.entry_price,
                sl=t.sl, target=t.target,
                exit_ts=t.exit_ts, exit_price=t.exit_price, exit_reason=t.exit_reason,
                qty=t.qty, rr_at_entry=t.rr_at_entry,
                pnl_raw=raw, pnl_leveraged=lev,
            ))

    return RunReport(signals=all_signals, trades=all_trades)


def commit_report(
    conn: sqlite3.Connection,
    report: RunReport,
    *,
    cfg: BacktestConfig,
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
