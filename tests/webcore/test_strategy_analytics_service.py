"""Strategy-analytics service — drill-downs + filters over the generic tables."""
from __future__ import annotations

import sqlite3

import pytest

from nse_data.webcore.errors import BadRequest, NotFound, Unavailable
from nse_data.webcore.repositories.strategy_analytics import StrategyAnalyticsRepository
from nse_data.webcore.services.strategy_analytics import StrategyAnalyticsService


def _svc(conn):
    return StrategyAnalyticsService(StrategyAnalyticsRepository(conn))


def _seed():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(open("migrations/096_strategy_analytics.sql").read())
    conn.execute("INSERT INTO strategy_runs (id, strategy, n_symbols, n_trades, win_rate, "
                 "profit_factor, net_pct, params_json) VALUES (1,'daily_sweep',2,3,0.667,2.0,5.0,'{\"rr_target\":3}')")
    rows = [  # (symbol, dir, entry_ts, exit_ts, net_pnl, net_pct, exit_reason, regime)
        ("RELIANCE", "long", "2024-03-01T10:00:00+05:30", "2024-03-01T11:00:00+05:30", 1000, 1.0, "target", "bull_2023_24"),
        ("RELIANCE", "short", "2025-02-01T14:00:00+05:30", "2025-02-01T15:00:00+05:30", -400, -0.4, "stop", "current_2025+"),
        ("TCS", "long", "2024-06-01T10:00:00+05:30", "2024-06-01T11:00:00+05:30", 800, 0.8, "target", "bull_2023_24"),
    ]
    for sym, d, en, ex, pnl, pct, rsn, reg in rows:
        conn.execute("INSERT INTO strategy_trades (run_id, strategy, symbol, direction, entry_ts, "
                     "exit_ts, net_pnl, net_pct, exit_reason, regime, rr_achieved) "
                     "VALUES (1,'daily_sweep',?,?,?,?,?,?,?,?,1.0)",
                     (sym, d, en, ex, pnl, pct, rsn, reg))
    conn.commit()
    return conn


def test_strategies_and_runs():
    s = _svc(_seed())
    assert s.strategies()["strategies"][0]["strategy"] == "daily_sweep"
    run = s.list_runs()["runs"][0]
    assert run["n_trades"] == 3 and run["win_rate_pct"] == 66.7


def test_pnl_drilldowns():
    s = _svc(_seed())
    by_sym = {b["bucket"]: b["pnl"] for b in s.pnl_by(1, "symbol")["buckets"]}
    assert by_sym == {"RELIANCE": 600.0, "TCS": 800.0}            # 1000−400, 800
    by_reg = {b["bucket"]: b["pnl"] for b in s.pnl_by(1, "regime")["buckets"]}
    assert by_reg == {"bull_2023_24": 1800.0, "current_2025+": -400.0}
    by_dir = {b["bucket"]: b["trades"] for b in s.pnl_by(1, "direction")["buckets"]}
    assert by_dir == {"long": 2, "short": 1}
    months = [b["bucket"] for b in s.pnl_by(1, "month")["buckets"]]
    assert "2024-03" in months and "2025-02" in months


def test_filters_and_errors():
    s = _svc(_seed())
    assert s.trades(1, symbol="RELIANCE")["count"] == 2
    assert s.trades(1, direction="short")["count"] == 1
    assert s.trades(1, exit_reason="target")["count"] == 2
    assert len(s.get_run(1)["equity_curve"]) == 3
    with pytest.raises(BadRequest):
        s.pnl_by(1, "not_a_dim")
    with pytest.raises(NotFound):
        s.get_run(999)
    with pytest.raises(Unavailable):
        _svc(sqlite3.connect(":memory:")).strategies()
