"""Run the Daily Sweep backtest over a symbol set and persist into the generic strategy tables.

This is the only strategy-specific glue: map `TradeResult` → the common trade dict. A future
strategy writes its own tiny adapter; everything downstream (UI, drill-downs) is shared.
"""
from __future__ import annotations

from dataclasses import asdict

from ..persist import persist_run, regime_for
from .backtest import _metrics, run_backtest
from .config import DailySweepConfig


def _trade_dict(t) -> dict:
    s = t.setup
    return {
        "symbol": s.symbol, "direction": s.direction, "daily_trend": s.daily_trend,
        "sweep_ts": s.sweep_time.isoformat(), "bos_ts": s.bos_time.isoformat(),
        "fvg_low": s.fvg_low, "fvg_high": s.fvg_high,
        "entry_ts": s.entry_time.isoformat(), "exit_ts": t.exit_time.isoformat(),
        "entry_price": s.entry_price, "exit_price": t.exit_price, "stop": s.stop,
        "target": s.target, "qty": s.qty, "rr": s.rr, "rr_achieved": t.rr_achieved,
        "net_pnl": t.net_pnl, "net_pct": t.net_pct, "exit_reason": t.exit_reason,
        "regime": regime_for(s.entry_time.date().isoformat()),
    }


def run_and_persist(conn, symbols, config: DailySweepConfig, *, notes: str | None = None) -> int:
    """Backtest each symbol, pool the trades, persist one run. Returns run_id."""
    all_trades, syms_with = [], 0
    for sym in symbols:
        try:
            rep = run_backtest(conn, sym, config)
        except Exception:  # noqa: BLE001 — one symbol shouldn't sink the run
            continue
        if rep["trades"]:
            syms_with += 1
        all_trades += rep["trades"]
    metrics = _metrics(all_trades)
    metrics["n_symbols"] = syms_with
    dates = sorted(t.setup.entry_time.date().isoformat() for t in all_trades) or [None]
    return persist_run(conn, strategy="daily_sweep", params=asdict(config), metrics=metrics,
                       trades=[_trade_dict(t) for t in all_trades],
                       start_date=dates[0], end_date=dates[-1], notes=notes)
