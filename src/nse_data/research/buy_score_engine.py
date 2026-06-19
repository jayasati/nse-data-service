"""Backtest adapter — exposes the integrated Buy Score as a standard
score_universe(conn, symbols, as_of_ep, sector_of) engine so it plugs into
backtest_engine.py (cross-sectional bucket / Rank-IC) and backtest_strategy_pit.py
(dynamic). Answers #4: does the regime-adaptive Buy Score blend beat plain Q+V+Mom?

The cross-sectional Buy Score is buy_raw() over every engine's factors (+ sector flow),
risk-multiplied. Macro is a market-level overlay (same for all names that day) so it
doesn't change cross-sectional ranking and is omitted here; velocity is path-dependent
and not a cross-sectional factor. Uses neutral regime weights for an apples-to-apples
test (the regime-adaptive variant is a separate study). ETFs/funds are excluded by
requiring an Opportunity (Quality or Valuation) score.
"""
from __future__ import annotations

from . import snapshot, buy_score as bs

WEIGHTS = bs.REGIME_WEIGHTS["neutral"]


def score_universe(conn, symbols, as_of_ep, sector_of) -> dict:
    rows = snapshot.compute_snapshot(conn, symbols, as_of_ep, sector_of)
    out = {}
    for s, f in rows.items():
        if bs._opportunity(f) is None:                  # require a fundamental → no funds/ETFs
            continue
        if f.get("sector_flow") is None:
            f = {**f, "sector_flow": bs.sector_flow_score(conn, f.get("sector"), as_of_ep)}
        buy, _ = bs.buy_raw(f, WEIGHTS)
        if buy is not None:
            out[s] = {"score": buy}
    return out
