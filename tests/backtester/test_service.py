"""BacktestService — aggregation and shape contracts."""

from __future__ import annotations

import pytest

from nse_data.backtester._core.persistence import write_run
from nse_data.backtester._core.types import SymbolTrade
from nse_data.backtester.strategies.bb_ema9_30m.config import BacktestConfig
from nse_data.webcore.errors import NotFound
from nse_data.webcore.repositories.backtests import BacktestRepository
from nse_data.webcore.services.backtests import BacktestService


def _trade(symbol: str, pnl_raw: float, entry_ts: int, direction: str = "LONG"):
    return SymbolTrade(
        symbol=symbol, direction=direction,
        setup_ts=entry_ts - 1800, entry_ts=entry_ts,
        entry_price=100.0, sl=99.0, target=102.0,
        exit_ts=entry_ts + 3600, exit_price=100.0 + pnl_raw,
        exit_reason="TARGET" if pnl_raw > 0 else "STOP",
        qty=1, rr_at_entry=2.0,
        pnl_raw=pnl_raw, pnl_leveraged=pnl_raw * 5.0,
    )


@pytest.fixture
def seeded_db(backtest_db):
    """Seed DB with two runs and a few trades."""
    backtest_db.row_factory = __import__("sqlite3").Row
    cfg = BacktestConfig(leverage=5.0)

    run1_id = write_run(
        backtest_db, cfg=cfg, universe="test1", symbols_count=2,
        start_date="2026-01-01", end_date="2026-01-10",
        trades=[
            _trade("RELIANCE", 10.0, 1_700_000_000),
            _trade("TCS",      -4.0, 1_700_003_600),
            _trade("RELIANCE",  6.0, 1_700_007_200),
        ],
        total_signals=5,
        notes="seeded for service tests",
    )
    run2_id = write_run(
        backtest_db, cfg=cfg, universe="test2", symbols_count=1,
        start_date="2026-02-01", end_date="2026-02-05",
        trades=[_trade("INFY", 3.0, 1_700_100_000)],
        total_signals=1,
    )
    return backtest_db, run1_id, run2_id


def _svc(conn) -> BacktestService:
    return BacktestService(BacktestRepository(conn))


def test_list_runs_returns_newest_first(seeded_db):
    conn, run1, run2 = seeded_db
    result = _svc(conn).list_runs(limit=10)

    assert result["count"] == 2
    ids = [r["id"] for r in result["runs"]]
    assert ids[0] == run2          # newest
    assert ids[1] == run1


def test_get_run_includes_equity_curve_and_win_rate(seeded_db):
    conn, run1, _ = seeded_db
    run = _svc(conn).get_run(run1)

    # 3 trades: +10, -4, +6 -> cumulative leveraged 50, 30, 60
    assert run["total_trades"] == 3
    assert run["wins"] == 2
    assert run["losses"] == 1
    assert run["win_rate"] == pytest.approx(66.7)
    assert len(run["equity_curve"]) == 3
    assert run["equity_curve"][0]["cum_leveraged"] == pytest.approx(50.0)
    assert run["equity_curve"][-1]["cum_leveraged"] == pytest.approx(60.0)
    assert "params" in run
    assert run["params"]["leverage"] == 5.0


def test_get_run_404_for_unknown(seeded_db):
    conn, *_ = seeded_db
    with pytest.raises(NotFound):
        _svc(conn).get_run(99999)


def test_trades_pagination_and_symbol_filter(seeded_db):
    conn, run1, _ = seeded_db
    # All 3 trades
    all_trades = _svc(conn).trades(run1)
    assert all_trades["count"] == 3

    # Filter by symbol
    reliance = _svc(conn).trades(run1, symbol="reliance")
    assert reliance["count"] == 2
    assert all(t["symbol"] == "RELIANCE" for t in reliance["trades"])

    # Pagination
    page1 = _svc(conn).trades(run1, limit=2, offset=0)
    page2 = _svc(conn).trades(run1, limit=2, offset=2)
    assert page1["count"] == 2
    assert page2["count"] == 1


def test_by_symbol_rollup_aggregates_correctly(seeded_db):
    conn, run1, _ = seeded_db
    result = _svc(conn).by_symbol(run1)

    by_sym = {r["symbol"]: r for r in result["by_symbol"]}
    assert by_sym["RELIANCE"]["trades"] == 2
    assert by_sym["RELIANCE"]["wins"] == 2
    assert by_sym["RELIANCE"]["losses"] == 0
    assert by_sym["RELIANCE"]["win_rate"] == 100.0
    assert by_sym["RELIANCE"]["pnl_leveraged"] == pytest.approx(80.0)  # (10+6)*5

    assert by_sym["TCS"]["trades"] == 1
    assert by_sym["TCS"]["wins"] == 0
    assert by_sym["TCS"]["losses"] == 1
    assert by_sym["TCS"]["win_rate"] == 0.0
