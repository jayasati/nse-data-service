"""Strategy registry — resolution + dispatch."""

from __future__ import annotations

import pytest

from nse_data.backtester.strategies.registry import STRATEGIES, resolve


def test_both_strategies_registered():
    assert "bb_ema9_30m" in STRATEGIES
    assert "macd_willr_daily" in STRATEGIES


def test_resolve_returns_engine_fn_and_config_cls():
    spec_v1 = resolve("bb_ema9_30m")
    spec_v2 = resolve("macd_willr_daily")

    # Engine fn callable
    assert callable(spec_v1.engine_fn)
    assert callable(spec_v2.engine_fn)

    # Config can be instantiated with no args
    cfg_v1 = spec_v1.config_cls()
    cfg_v2 = spec_v2.config_cls()
    assert cfg_v1.strategy == "bb_ema9_30m"
    assert cfg_v2.strategy == "macd_willr_daily"


def test_resolve_raises_keyerror_on_unknown():
    with pytest.raises(KeyError):
        resolve("nonexistent_strategy")
