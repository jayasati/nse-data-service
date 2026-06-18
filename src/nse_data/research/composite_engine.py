"""Composite of VALIDATED engines (currently Quality + Valuation, equal-weight).
Tests whether they STACK (independent → bigger spread than either alone) or are
redundant. Weights stay equal until backtests justify data-driven weights; new
engines join here only after they validate.
"""
from __future__ import annotations

from . import quality_engine, valuation_engine, turnaround_engine

# validated engines only (Momentum excluded — failed its gate). Turnaround is on
# trial: included iff Q+V+T beats Q+V (else it dilutes the equal-weight mean).
ENGINES = (("quality", quality_engine), ("valuation", valuation_engine),
           ("turnaround", turnaround_engine))


def score_universe(conn, symbols, as_of_ep, sector_of):
    parts = {name: mod.score_universe(conn, symbols, as_of_ep, sector_of)
             for name, mod in ENGINES}
    out = {}
    for s in set().union(*[set(p) for p in parts.values()]):
        comps = {name: parts[name][s]["score"] for name, _ in ENGINES if s in parts[name]}
        if comps:
            out[s] = {"score": round(sum(comps.values()) / len(comps), 1), "components": comps}
    return out
