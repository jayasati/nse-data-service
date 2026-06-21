"""Strategy-analytics service — shapes repo rows into JSON for the API/UI.

No web imports; raises domain errors (Unavailable/NotFound/BadRequest) the route layer maps to
HTTP. This is the read-side of the generic strategy backtest study.
"""
from __future__ import annotations

import json

from ..errors import BadRequest, NotFound, Unavailable
from ..repositories.strategy_analytics import DIMENSIONS, StrategyAnalyticsRepository


class StrategyAnalyticsService:
    def __init__(self, repo: StrategyAnalyticsRepository):
        self.repo = repo

    def _ready(self):
        if not self.repo.tables_ready():
            raise Unavailable("strategy_runs/strategy_trades not present (migration 096 not applied?)")

    def strategies(self) -> dict:
        self._ready()
        rows = self.repo.list_strategies()
        return {"count": len(rows), "strategies": [dict(r) for r in rows]}

    def list_runs(self, limit: int = 20, strategy: str | None = None) -> dict:
        self._ready()
        rows = self.repo.list_runs(limit, strategy)
        return {"count": len(rows), "runs": [self._run_row(r) for r in rows]}

    def get_run(self, run_id: int) -> dict:
        self._ready()
        r = self.repo.get_run(run_id)
        if r is None:
            raise NotFound(f"run {run_id} not found")
        out = self._run_row(r)
        out["params"] = json.loads(r["params_json"]) if r["params_json"] else {}
        cum, curve = 0.0, []
        for e in self.repo.equity_curve(run_id):
            cum += e["net_pnl"] or 0.0
            curve.append({"ts": e["exit_ts"], "cum_pnl": round(cum, 2)})
        out["equity_curve"] = curve
        return out

    def trades(self, run_id: int, *, symbol=None, direction=None, exit_reason=None,
               limit: int = 200, offset: int = 0) -> dict:
        self._ready()
        rows = self.repo.trades(run_id, symbol=symbol, direction=direction,
                                exit_reason=exit_reason, limit=limit, offset=offset)
        return {"run_id": run_id, "count": len(rows), "limit": limit, "offset": offset,
                "trades": [dict(r) for r in rows]}

    def live_signals(self, limit: int = 100) -> dict:
        rows = self.repo.live_signals(limit)            # daily_sweep_signals; [] if absent
        return {"count": len(rows), "signals": [dict(r) for r in rows]}

    def pnl_by(self, run_id: int, dimension: str) -> dict:
        self._ready()
        if dimension not in DIMENSIONS:
            raise BadRequest(f"unknown dimension '{dimension}'; one of {sorted(DIMENSIONS)}")
        rows = [self._bucket(r) for r in self.repo.pnl_by(run_id, dimension)]
        return {"run_id": run_id, "dimension": dimension, "count": len(rows), "buckets": rows}

    # ---- shaping ----
    @staticmethod
    def _run_row(r) -> dict:
        d = dict(r)
        n = d.get("n_trades") or 0
        wr = d.get("win_rate")
        d["win_rate_pct"] = round(wr * 100, 1) if wr is not None else None
        d["n_trades"] = n
        return d

    @staticmethod
    def _bucket(r) -> dict:
        d = dict(r)
        t = d.get("trades") or 0
        d["win_rate"] = round((d.get("wins") or 0) / t * 100, 1) if t else None
        return d
