"""Steps 10 & 11 — backtest: resolve each setup to an outcome, report metrics + per-trade rows.

Simulation is intraday (the strategy is session-based): from the entry bar, walk the SAME day's
5m bars and take whichever of target / stop is hit first — SL-first when a bar straddles both
(conservative, matches the existing backtester). Unfilled by the last session bar → time-exit at
close. P&L is net of the full cost model; metrics reuse `research.paper_report` / `deflated_sharpe`.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from ...costs.model import compute_costs
from ...research.deflated_sharpe import sharpe
from ...research.paper_report import max_drawdown, trade_metrics
from .config import DailySweepConfig
from .data import read_5m, read_daily, read_1h
from .setup import SweepSetup, scan_setups

_FLAT_BY = dt.time(15, 25)


@dataclass
class TradeResult:
    setup: SweepSetup
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: str          # 'target' | 'stop' | 'time'
    rr_achieved: float
    net_pnl: float
    net_pct: float


def _simulate(s: SweepSetup, m5: pd.DataFrame, *, segment: str) -> TradeResult | None:
    try:
        e = m5.index.get_loc(s.entry_time)
    except KeyError:
        return None
    is_long = s.direction == "long"
    risk = abs(s.entry_price - s.stop)
    exit_px = exit_reason = exit_time = None
    for j in range(e + 1, len(m5)):
        ts = m5.index[j]
        if ts.date() != s.entry_time.date():          # intraday — never cross into the next day
            break
        lo, hi = float(m5["low"].iloc[j]), float(m5["high"].iloc[j])
        if is_long:
            if lo <= s.stop:                            # SL-first when both touched in one bar
                exit_px, exit_reason = s.stop, "stop"
            elif hi >= s.target:
                exit_px, exit_reason = s.target, "target"
        else:
            if hi >= s.stop:
                exit_px, exit_reason = s.stop, "stop"
            elif lo <= s.target:
                exit_px, exit_reason = s.target, "target"
        if exit_px is not None:
            exit_time = ts
            break
        if ts.time() >= _FLAT_BY:                        # force flat at session end
            exit_px, exit_reason, exit_time = float(m5["close"].iloc[j]), "time", ts
            break
    if exit_px is None:                                 # ran out of same-day bars
        exit_px, exit_reason, exit_time = float(m5["close"].iloc[-1]), "time", m5.index[-1]
    tc = compute_costs(s.entry_price, exit_px, s.qty,
                       trade_type="long" if is_long else "short", segment=segment)
    net_pct = tc.net_pnl / (s.entry_price * s.qty) * 100.0
    rr = (exit_px - s.entry_price) / risk if is_long else (s.entry_price - exit_px) / risk
    return TradeResult(s, exit_time, float(exit_px), exit_reason, round(rr, 2),
                       round(tc.net_pnl, 2), round(net_pct, 4))


def run_backtest(conn, symbol: str, config: DailySweepConfig,
                 *, blocked_dates: set | None = None) -> dict:
    """Scan + simulate one symbol. Returns {symbol, metrics, trades}."""
    daily, h1, m5 = read_daily(conn, symbol), read_1h(conn, symbol), read_5m(conn, symbol)
    setups = scan_setups(daily, h1, m5, config=config, symbol=symbol, blocked_dates=blocked_dates)
    trades = [t for t in (_simulate(s, m5, segment=config.segment) for s in setups) if t]
    return {"symbol": symbol, "metrics": _metrics(trades), "trades": trades}


def _metrics(trades: list[TradeResult]) -> dict:
    rets = [t.net_pct for t in trades]
    m = trade_metrics(rets)
    if not trades:
        return m
    holds = [(t.exit_time - t.setup.entry_time).total_seconds() / 60 for t in trades]
    wins_rr = [t.rr_achieved for t in trades]
    by_reason = {}
    for t in trades:
        by_reason[t.exit_reason] = by_reason.get(t.exit_reason, 0) + 1
    m.update({
        "avg_rr": round(sum(wins_rr) / len(wins_rr), 2),
        "max_drawdown_pct": round(max_drawdown(rets), 2),
        "sharpe": round(sharpe(rets), 2) if len(rets) > 1 else None,
        "total_net_pct": round(sum(rets), 2),
        "avg_hold_min": round(sum(holds) / len(holds), 1),
        "exit_breakdown": by_reason,
    })
    return m


def format_report(rep: dict) -> str:
    """Step 11 — headline metrics + the per-trade table."""
    m, sym = rep["metrics"], rep["symbol"]
    if not rep["trades"]:
        return f"{sym}: 0 trades"
    L = [f"━━━ Daily Sweep — {sym} ━━━",
         f"trades {m['n']} | win {m['win_rate']:.0%} | avg RR {m['avg_rr']} | "
         f"PF {m['profit_factor']} | expectancy {m['expectancy']:.3f}% | "
         f"maxDD {m['max_drawdown_pct']}% | Sharpe {m['sharpe']} | net {m['total_net_pct']}% | "
         f"hold {m['avg_hold_min']}min | exits {m['exit_breakdown']}",
         "date        time   dir   entry     SL       target    exit      result  RR"]
    for t in rep["trades"]:
        s = t.setup
        L.append(f"{s.entry_time:%Y-%m-%d %H:%M} {s.direction:5} {s.entry_price:9.1f} "
                 f"{s.stop:8.1f} {s.target:9.1f} {t.exit_price:9.1f} {t.exit_reason:7} {t.rr_achieved:+.2f}")
    return "\n".join(L)
