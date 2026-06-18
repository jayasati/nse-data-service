"""Composite of VALIDATED engines (currently Quality + Valuation, equal-weight).
Tests whether they STACK (independent → bigger spread than either alone) or are
redundant. Weights stay equal until backtests justify data-driven weights; new
engines join here only after they validate.
"""
from __future__ import annotations

from . import quality_engine, valuation_engine

# Composite = engines that VALIDATE *and* ADD. Scorecard (2026-06-18):
#   Quality ✓ + Value ✓ → stack to +6.0%/60d monotonic on B-tradeable (KEPT).
#   Momentum ✗ — failed its gate (mean-reverting regime).
#   Turnaround ✓ standalone (+2.4%) but DILUTES Q+V (60d 6.0%→4.9%, breaks
#     monotonicity) — redundant with Quality → EXCLUDED.
ENGINES = (("quality", quality_engine), ("valuation", valuation_engine))


def score_universe(conn, symbols, as_of_ep, sector_of):
    parts = {name: mod.score_universe(conn, symbols, as_of_ep, sector_of)
             for name, mod in ENGINES}
    out = {}
    for s in set().union(*[set(p) for p in parts.values()]):
        comps = {name: parts[name][s]["score"] for name, _ in ENGINES if s in parts[name]}
        if comps:
            out[s] = {"score": round(sum(comps.values()) / len(comps), 1), "components": comps}
    return out
