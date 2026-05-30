"""Persistence round-trip for backtest_runs + backtest_trades."""

from __future__ import annotations

from nse_data.backtester._core.persistence import write_run
from nse_data.backtester._core.types import SymbolTrade
from nse_data.backtester.strategies.bb_ema9_30m.config import BacktestConfig


def _make_trade(symbol: str, pnl_raw: float, leverage: float = 5.0) -> SymbolTrade:
    return SymbolTrade(
        symbol=symbol, direction="LONG",
        setup_ts=1_700_000_000, entry_ts=1_700_001_800,
        entry_price=100.0, sl=99.0, target=102.0,
        exit_ts=1_700_005_400, exit_price=100.0 + pnl_raw,
        exit_reason="TARGET" if pnl_raw > 0 else "STOP",
        qty=1, rr_at_entry=2.0,
        pnl_raw=pnl_raw, pnl_leveraged=pnl_raw * leverage,
    )


def test_write_run_returns_id_and_persists_rows(backtest_db):
    cfg = BacktestConfig(leverage=5.0)
    trades = [
        _make_trade("RELIANCE", 10.0),
        _make_trade("TCS", -4.0),
        _make_trade("INFY", 6.0),
    ]

    run_id = write_run(
        backtest_db,
        cfg=cfg, universe="test", symbols_count=3,
        start_date="2026-01-01", end_date="2026-01-31",
        trades=trades, total_signals=5, notes="unit test",
    )

    assert run_id > 0

    row = backtest_db.execute(
        "SELECT total_signals, total_trades, wins, losses, pnl_raw, pnl_leveraged "
        "FROM backtest_runs WHERE id = ?", (run_id,),
    ).fetchone()
    assert row == (5, 3, 2, 1, 12.0, 60.0)   # 10 + (-4) + 6 = 12 ; 12 * 5 = 60

    n_trades = backtest_db.execute(
        "SELECT COUNT(*) FROM backtest_trades WHERE run_id = ?", (run_id,),
    ).fetchone()[0]
    assert n_trades == 3


def test_write_run_with_no_trades_still_creates_run(backtest_db):
    cfg = BacktestConfig()

    run_id = write_run(
        backtest_db,
        cfg=cfg, universe="empty", symbols_count=0,
        start_date="2026-01-01", end_date="2026-01-01",
        trades=[], total_signals=0,
    )

    row = backtest_db.execute(
        "SELECT total_trades, wins, losses, pnl_raw "
        "FROM backtest_runs WHERE id = ?", (run_id,),
    ).fetchone()
    assert row == (0, 0, 0, 0.0)


def test_max_drawdown_computed_correctly(backtest_db):
    cfg = BacktestConfig(leverage=1.0)
    # cumulative: 10, -5 (peak 10, dd -15), 8 (peak 10, dd -7), 20 (new peak)
    trades = [
        _make_trade("A", 10.0, leverage=1.0),
        _make_trade("B", -15.0, leverage=1.0),
        _make_trade("C", 3.0, leverage=1.0),
        _make_trade("D", 12.0, leverage=1.0),
    ]
    # Cumulative: 10, -5, -2, 10 → peak = 10 (after first), trough = -5 → max DD = -15

    run_id = write_run(
        backtest_db,
        cfg=cfg, universe="test", symbols_count=4,
        start_date="2026-01-01", end_date="2026-01-31",
        trades=trades, total_signals=4,
    )

    max_dd = backtest_db.execute(
        "SELECT max_dd_raw FROM backtest_runs WHERE id = ?", (run_id,),
    ).fetchone()[0]
    assert max_dd == -15.0


def test_params_json_round_trips(backtest_db):
    import json
    cfg = BacktestConfig(leverage=5.0, gap_pct=0.005, gap_mode="overnight",
                        rr_min=2.0, bb_length=14, bb_std=2.5, ema_length=21)

    run_id = write_run(
        backtest_db, cfg=cfg, universe="t", symbols_count=1,
        start_date="2026-01-01", end_date="2026-01-31",
        trades=[], total_signals=0,
    )

    params = json.loads(
        backtest_db.execute(
            "SELECT params_json FROM backtest_runs WHERE id = ?", (run_id,),
        ).fetchone()[0]
    )
    assert params["leverage"] == 5.0
    assert params["gap_pct"] == 0.005
    assert params["gap_mode"] == "overnight"
    assert params["bb_length"] == 14
    assert params["bb_std"] == 2.5
    assert params["ema_length"] == 21
