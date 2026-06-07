"""Intraday equity cost model (costs/model.py)."""

from __future__ import annotations

import pytest

from nse_data.costs import model
from nse_data.costs.model import compute_costs


def test_gross_pnl_long_and_short():
    assert compute_costs(100.0, 110.0, 100, "long").gross_pnl == pytest.approx(1000.0)
    assert compute_costs(100.0, 110.0, 100, "short").gross_pnl == pytest.approx(-1000.0)
    assert compute_costs(110.0, 100.0, 100, "short").gross_pnl == pytest.approx(1000.0)


def test_net_is_gross_minus_total_costs():
    c = compute_costs(100.0, 110.0, 100, "long")
    assert c.net_pnl == pytest.approx(c.gross_pnl - c.total_costs)
    assert c.total_costs == pytest.approx(
        c.brokerage + c.stt + c.exchange + c.sebi + c.stamp_duty + c.gst + c.slippage
    )


def test_cost_components_match_schedule():
    # buy 100 @ ₹500 -> ₹50k; sell 100 @ ₹520 -> ₹52k.
    c = compute_costs(500.0, 520.0, 100, "long")
    buy_val, sell_val = 50_000.0, 52_000.0

    # Brokerage: min(20, 0.03% * val) per leg -> 0.03% of 50k = 15, of 52k = 15.6.
    assert c.brokerage == pytest.approx(15.0 + 15.6)
    assert c.stt == pytest.approx(0.00025 * sell_val)             # sell side only
    assert c.exchange == pytest.approx(0.0000345 * (buy_val + sell_val))
    assert c.sebi == pytest.approx(0.000001 * (buy_val + sell_val))
    assert c.stamp_duty == pytest.approx(0.00003 * buy_val)       # buy side only
    assert c.gst == pytest.approx(0.18 * (c.brokerage + c.exchange + c.sebi))


def test_brokerage_capped_at_20_per_leg():
    # Large turnover: 0.03% would exceed ₹20, so each leg caps at ₹20 -> ₹40 total.
    c = compute_costs(1000.0, 1000.0, 1000, "long")   # ₹10L per leg
    assert c.brokerage == pytest.approx(2 * model.BROKERAGE_CAP)


def test_slippage_tick_floor_vs_bps():
    # Cheap stock: 1 bps of ₹10 = ₹0.001 < tick 0.05 -> tick dominates.
    cheap = compute_costs(10.0, 10.0, 100, "long")
    assert cheap.slippage == pytest.approx(2 * model.TICK_SIZE * 100)
    # Expensive stock: 1 bps of ₹5000 = ₹0.50 > tick -> bps dominates.
    pricey = compute_costs(5000.0, 5000.0, 1, "long")
    assert pricey.slippage == pytest.approx(2 * model.SLIPPAGE_BPS * 5000.0 * 1)


def test_invalid_trade_type():
    with pytest.raises(ValueError):
        compute_costs(100.0, 110.0, 10, "sideways")
