"""
Backtest performance metrics (FEATURE_CHECKLIST Phase 3, Week 10).

Turns a list of trades into the numbers the registry + LEARNINGS table need:
win rate, avg win/loss, profit factor, max drawdown, and **Sharpe on daily
portfolio returns annualised by √252** (the agreed basis).

Sharpe needs a return *series*, not per-trade P&L, so trades are bucketed into
daily P&L by exit date and divided by a capital base (the per-trade notional) to
get a unit-capital daily return. Gross (pnl_raw) and net (pnl_net) are computed
side by side; the gap is the cost drag.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from ...scheduler.market_hours import IST

TRADING_DAYS = 252


def _daily_pnl(trades, attr: str) -> list[float]:
    """Per-day summed P&L, ordered by date (exit day buckets)."""
    by_day: dict = defaultdict(float)
    for t in trades:
        day = datetime.fromtimestamp(t.exit_ts, tz=IST).date()
        by_day[day] += getattr(t, attr)
    return [by_day[d] for d in sorted(by_day)]


def sharpe(returns: list[float], periods: int = TRADING_DAYS) -> float:
    """Annualised Sharpe of a return series (rf=0). 0.0 if undefined."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0
    return (mean / sd) * math.sqrt(periods)


def daily_sharpe(trades, capital: float, attr: str) -> float:
    if capital <= 0:
        return 0.0
    returns = [p / capital for p in _daily_pnl(trades, attr)]
    return round(sharpe(returns), 3)


def profit_factor(pnls: list[float]) -> float | None:
    """Gross profit / gross loss. None if there are no losses (undefined)."""
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    if gross_loss == 0:
        return None
    return round(gross_profit / gross_loss, 3)


def max_drawdown_inr(pnls: list[float]) -> float:
    """Worst peak-to-trough on cumulative P&L, in absolute INR (<= 0).

    Capital-independent and unambiguous — the headline drawdown metric. (A
    percent drawdown needs a real account size; with hundreds of overlapping
    trades the per-trade notional is the wrong base, so % is reported separately
    and clearly caveated.)
    """
    cum, peak, worst = 0.0, 0.0, 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return round(worst, 2)


def max_drawdown_pct(pnls: list[float], capital: float) -> float:
    """Worst peak-to-trough as a negative percent of an equity curve that starts
    at `capital`. ONLY meaningful when `capital` approximates the real account
    size; otherwise read `max_drawdown_inr`."""
    if capital <= 0:
        return 0.0
    equity, peak, worst = capital, capital, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        worst = min(worst, (equity - peak) / peak)
    return round(worst * 100, 2)


def _side(pnls: list[float]) -> tuple[float, float, float]:
    """(win_rate, avg_win, avg_loss) over decided trades."""
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    decided = len(wins) + len(losses)
    win_rate = round(100 * len(wins) / decided, 1) if decided else 0.0
    avg_win = round(sum(wins) / len(wins), 2) if wins else 0.0
    avg_loss = round(sum(losses) / len(losses), 2) if losses else 0.0
    return win_rate, avg_win, avg_loss


def summarize(trades, *, capital: float) -> dict:
    """Full gross + net metric set for one set of trades."""
    trades = list(trades)
    raw = [t.pnl_raw for t in trades]
    net = [t.pnl_net for t in trades]
    win_rate, avg_win, avg_loss = _side(net)

    gross_sharpe = daily_sharpe(trades, capital, "pnl_raw")
    net_sharpe = daily_sharpe(trades, capital, "pnl_net")

    return {
        "n_trades": len(trades),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor(net),
        "gross_pnl": round(sum(raw), 2),
        "net_pnl": round(sum(net), 2),
        "gross_sharpe": gross_sharpe,
        "net_sharpe": net_sharpe,
        "cost_drag_sharpe": round(gross_sharpe - net_sharpe, 3),
        "max_drawdown_inr": max_drawdown_inr(net),
        "max_drawdown_pct": max_drawdown_pct(net, capital),
    }
