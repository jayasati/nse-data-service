"""Unit tests for backtester._core.metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from nse_data.backtester._core import metrics

_IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class _T:
    exit_ts: int
    pnl_raw: float
    pnl_net: float


def _ts(y, m, d) -> int:
    return int(datetime(y, m, d, 15, 20, tzinfo=_IST).timestamp())


def test_sharpe_zero_when_constant():
    assert metrics.sharpe([0.01, 0.01, 0.01]) == 0.0    # zero variance


def test_sharpe_positive_for_upward_series():
    s = metrics.sharpe([0.01, 0.02, 0.015, 0.03])
    assert s > 0


def test_profit_factor():
    assert metrics.profit_factor([100, -50, 50]) == 3.0   # 150 / 50
    assert metrics.profit_factor([100, 50]) is None        # no losses


def test_max_drawdown_inr():
    # +100 (peak 100), -300 (cum -200) -> worst trough = -300
    assert metrics.max_drawdown_inr([100, -300]) == -300.0


def test_max_drawdown_pct():
    # capital 1000; +100 (1100 peak), -300 (800) -> dd = (800-1100)/1100 = -27.27%
    assert metrics.max_drawdown_pct([100, -300], capital=1000) == -27.27


def test_summarize_gross_vs_net():
    trades = [
        _T(_ts(2026, 6, 1), 200, 150),
        _T(_ts(2026, 6, 2), -100, -130),
        _T(_ts(2026, 6, 3), 300, 250),
    ]
    m = metrics.summarize(trades, capital=100_000)
    assert m["n_trades"] == 3
    assert m["gross_pnl"] == 400 and m["net_pnl"] == 270
    assert m["win_rate"] == round(100 * 2 / 3, 1)
    # cost drag: gross sharpe should be >= net sharpe here (costs reduce returns)
    assert m["gross_sharpe"] >= m["net_sharpe"]
    assert m["profit_factor"] == round(400 / 130, 3)
