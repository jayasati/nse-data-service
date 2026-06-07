"""Unit tests for the experiment registry + CPCV fold splitting (Week 11)."""

from __future__ import annotations

from datetime import date

from nse_data.backtester._core import cpcv, registry
from nse_data.backtester.strategies.macd_willr_daily.config import MacdWillrDailyConfig


# ---- registry --------------------------------------------------------------

def test_param_hash_is_stable_and_param_sensitive():
    a = MacdWillrDailyConfig(strategy="macd_willr_daily")
    b = MacdWillrDailyConfig(strategy="macd_willr_daily")
    c = MacdWillrDailyConfig(strategy="macd_willr_daily", leverage=2.0)
    assert registry.param_hash(a) == registry.param_hash(b)   # same params -> same hash
    assert registry.param_hash(a) != registry.param_hash(c)   # different params -> different


def test_decide_verdict():
    assert registry.decide_verdict(0.8, 0.3) == "promoted"     # both pass
    assert registry.decide_verdict(0.8, -0.1) == "shelved"     # cpcv negative
    assert registry.decide_verdict(-0.5, 0.3) == "shelved"     # net negative
    assert registry.decide_verdict(0.3, 0.3) == "needs_work"   # 0 < net <= 0.5


def test_record_run_persists(backtest_db):
    cfg = MacdWillrDailyConfig(strategy="macd_willr_daily")
    m = {"net_sharpe": -2.0, "gross_sharpe": -0.3, "win_rate": 50.0,
         "profit_factor": 0.8, "max_drawdown_pct": -40.0, "n_trades": 100,
         "cost_drag_sharpe": 1.7}
    rid = registry.record_run(
        backtest_db, run_date="2026-06-07", strategy_name="macd_willr_daily",
        cfg=cfg, date_range="full", metrics=m,
        cpcv_avg_sharpe=-1.5, cpcv_folds_pos=1, notes="test",
    )
    row = backtest_db.execute(
        "SELECT strategy_name, net_sharpe, verdict FROM backtest_registry WHERE id=?",
        (rid,),
    ).fetchone()
    assert row[0] == "macd_willr_daily" and row[1] == -2.0
    assert row[2] == "shelved"        # auto-verdict from net -2.0


# ---- CPCV fold splitting ---------------------------------------------------

def test_split_folds_contiguous_and_covers_range():
    folds = cpcv.split_folds(date(2026, 1, 1), date(2026, 12, 31), n_folds=10)
    assert len(folds) == 10
    assert folds[0][0] == date(2026, 1, 1)
    assert folds[-1][1] == date(2026, 12, 31)
    # contiguous: each fold starts the day after the previous ends
    from datetime import timedelta
    for (_, prev_end), (next_start, _) in zip(folds, folds[1:]):
        assert next_start == prev_end + timedelta(days=1)


def test_split_folds_clamps_to_available_days():
    folds = cpcv.split_folds(date(2026, 1, 1), date(2026, 1, 3), n_folds=10)
    assert len(folds) == 3        # can't make more folds than days
