"""TradesService — paper-trade aggregation + shape contracts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nse_data.webcore.errors import Unavailable
from nse_data.webcore.repositories.trades import TradesRepository
from nse_data.webcore.services.trades import TradesService

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "036_signals.sql"


def _insert(conn, *, symbol, strategy, status, net_pnl=None, gross_pnl=None,
            entry_time="2026-06-01T10:00:00+05:30", exit_time=None, reason=None):
    conn.execute(
        "INSERT INTO paper_trades "
        "(symbol, signal_type, entry_price, sl_price, t1_price, entry_time, "
        " exit_price, exit_time, exit_reason, gross_pnl, net_pnl, status) "
        "VALUES (?, ?, 100, 97, 103, ?, ?, ?, ?, ?, ?, ?)",
        (symbol, strategy, entry_time,
         None if net_pnl is None else 100 + net_pnl, exit_time, reason,
         gross_pnl, net_pnl, status),
    )
    conn.commit()


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(MIGRATION.read_text())
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def seeded(db) -> sqlite3.Connection:
    # long_buildup: 2 wins, 1 loss (closed) + 1 open
    _insert(db, symbol="A", strategy="long_buildup", status="closed", net_pnl=200, gross_pnl=210, reason="hit_t1")
    _insert(db, symbol="B", strategy="long_buildup", status="closed", net_pnl=150, gross_pnl=160, reason="hit_t1")
    _insert(db, symbol="C", strategy="long_buildup", status="closed", net_pnl=-100, gross_pnl=-90, reason="hit_sl")
    _insert(db, symbol="D", strategy="long_buildup", status="open")
    # breakout_52wh: 1 loss (closed)
    _insert(db, symbol="E", strategy="breakout_52wh", status="closed", net_pnl=-50, gross_pnl=-40, reason="forced_flat")
    return db


def _svc(conn) -> TradesService:
    return TradesService(TradesRepository(conn))


def test_overview_counts_and_winrate(seeded):
    o = _svc(seeded).overview()
    assert o["total"] == 5
    assert o["open"] == 1
    assert o["closed"] == 4
    assert o["wins"] == 2
    assert o["losses"] == 2
    assert o["win_rate"] == pytest.approx(50.0)         # 2 of 4 decided
    assert o["net_pnl"] == pytest.approx(200 + 150 - 100 - 50)


def test_by_strategy_breakdown(seeded):
    rows = {r["strategy"]: r for r in _svc(seeded).by_strategy()["by_strategy"]}

    lb = rows["long_buildup"]
    assert lb["total"] == 4 and lb["open"] == 1 and lb["closed"] == 3
    assert lb["wins"] == 2 and lb["losses"] == 1
    assert lb["win_rate"] == pytest.approx(66.7)
    assert lb["net_pnl"] == pytest.approx(250.0)
    assert lb["avg_win"] == pytest.approx(175.0)        # (200+150)/2
    assert lb["avg_loss"] == pytest.approx(-100.0)

    bo = rows["breakout_52wh"]
    assert bo["win_rate"] == 0.0
    assert bo["avg_win"] is None                          # no winners yet


def test_list_trades_filters(seeded):
    svc = _svc(seeded)
    assert svc.list_trades()["count"] == 5
    assert svc.list_trades(status="open")["count"] == 1
    assert svc.list_trades(status="closed")["count"] == 4
    assert svc.list_trades(strategy="breakout_52wh")["count"] == 1
    # open trades carry no P&L
    open_trade = svc.list_trades(status="open")["trades"][0]
    assert open_trade["net_pnl"] is None and open_trade["status"] == "open"


def test_empty_db_is_zeros_not_error(db):
    o = _svc(db).overview()
    assert o == {"total": 0, "open": 0, "closed": 0, "wins": 0, "losses": 0,
                 "win_rate": 0.0, "net_pnl": 0.0, "gross_pnl": 0.0}
    assert _svc(db).by_strategy()["by_strategy"] == []


def test_unavailable_when_table_missing():
    bare = sqlite3.connect(":memory:")
    bare.row_factory = sqlite3.Row
    with pytest.raises(Unavailable):
        _svc(bare).overview()
