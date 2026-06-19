"""Backtest adapter — Buy Score with REGIME-ADAPTIVE weights (vs the fixed-neutral
buy_score_engine). Each date's market regime is classified point-in-time from Nifty
trend + VIX, and the matching REGIME_WEIGHTS preset is applied (bull → trend/catalyst
heavier; bear/panic → quality-value heavier, trend de-emphasised).

Validates the v2 claim "weights must adapt to the regime" by comparing this engine to
the fixed-neutral one in backtest_engine + backtest_strategy_pit. Same fund/ETF guard.
"""
from __future__ import annotations

from . import snapshot, buy_score as bs, macro_engine


def score_universe(conn, symbols, as_of_ep, sector_of) -> dict:
    regime = macro_engine.nifty_regime(conn, as_of_ep)
    weights = bs.REGIME_WEIGHTS.get(regime, bs.REGIME_WEIGHTS["neutral"])
    rows = snapshot.compute_snapshot(conn, symbols, as_of_ep, sector_of)
    out = {}
    for s, f in rows.items():
        if bs._opportunity(f) is None:                  # require a fundamental → no funds/ETFs
            continue
        if f.get("sector_flow") is None:
            f = {**f, "sector_flow": bs.sector_flow_score(conn, f.get("sector"), as_of_ep)}
        buy, _ = bs.buy_raw(f, weights)
        if buy is not None:
            out[s] = {"score": buy}
    return out
