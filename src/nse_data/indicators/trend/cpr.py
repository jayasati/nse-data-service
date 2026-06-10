"""
CPR (Central Pivot Range) + floor pivots — tomorrow's map from today's bar.

For each session the levels are computed from the PREVIOUS session's H/L/C:

    pivot = (H + L + C) / 3          BC = (H + L) / 2          TC = 2·pivot − BC
    r1 = 2·pivot − L     s1 = 2·pivot − H
    r2 = pivot + (H − L) s2 = pivot − (H − L)

`cpr_width_pct` = |TC − BC| / pivot × 100 — the day-type tell: a narrow CPR
(≲0.3–0.5%) after a compressed day marks trending-day potential; a wide CPR
marks rotation/chop. The row stored at `date` carries the levels VALID FOR
that date (i.e., derived from the prior row), so a join on date gives the
chart/decision path the right lines with no shifting at read time.
"""

from __future__ import annotations

import pandas as pd

from ..base import Indicator


class CentralPivotRange(Indicator):
    name = "cpr"
    table = "indicator_cpr"
    pk_cols = ("symbol", "date")
    output_columns = ("cpr_pivot", "cpr_tc", "cpr_bc", "cpr_width_pct",
                      "r1", "s1", "r2", "s2")
    min_history = 2          # needs exactly the prior bar
    pane = "overlay"
    cadence = "eod"

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        h, l, c = ohlcv["high"].shift(1), ohlcv["low"].shift(1), ohlcv["close"].shift(1)
        result = pd.DataFrame(index=ohlcv.index)
        pivot = (h + l + c) / 3.0
        bc = (h + l) / 2.0
        tc = 2.0 * pivot - bc
        result["cpr_pivot"] = pivot
        result["cpr_tc"] = tc
        result["cpr_bc"] = bc
        result["cpr_width_pct"] = ((tc - bc).abs() / pivot * 100.0).where(pivot > 0)
        result["r1"] = 2.0 * pivot - l
        result["s1"] = 2.0 * pivot - h
        result["r2"] = pivot + (h - l)
        result["s2"] = pivot - (h - l)
        return result
