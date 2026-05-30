"""Backtest read service — shapes repository rows into JSON the dashboard wants.

No SQL here, no FastAPI imports. Raises domain errors that the route layer
translates to HTTP status codes.
"""

from __future__ import annotations

import json

from ..errors import NotFound, Unavailable
from ..repositories.backtests import BacktestRepository


class BacktestService:
    def __init__(self, repo: BacktestRepository):
        self.repo = repo

    def list_runs(self, limit: int = 20) -> dict:
        if not self.repo.tables_ready():
            raise Unavailable("backtest tables not present (migration 032 not applied?)")
        rows = self.repo.list_runs(limit)
        return {
            "count": len(rows),
            "runs": [self._run_summary(r) for r in rows],
        }

    def get_run(self, run_id: int) -> dict:
        if not self.repo.tables_ready():
            raise Unavailable("backtest tables not present")
        row = self.repo.get_run(run_id)
        if row is None:
            raise NotFound(f"run {run_id} not found")

        run = self._run_summary(row)
        run["params"] = json.loads(row["params_json"])

        # Equity curve from all trades in entry order.
        eq_rows = self.repo.trades_for_equity_curve(run_id)
        cum_raw, cum_lev = 0.0, 0.0
        curve = []
        for r in eq_rows:
            cum_raw += float(r["pnl_raw"])
            cum_lev += float(r["pnl_leveraged"])
            curve.append({
                "ts": int(r["entry_ts"]),
                "cum_raw": round(cum_raw, 2),
                "cum_leveraged": round(cum_lev, 2),
            })
        run["equity_curve"] = curve

        decided = run["wins"] + run["losses"]
        run["win_rate"] = round(run["wins"] / decided * 100, 1) if decided else 0.0

        return run

    def trades(
        self, run_id: int, *,
        symbol: str | None = None,
        limit: int = 100, offset: int = 0,
    ) -> dict:
        if not self.repo.tables_ready():
            raise Unavailable("backtest tables not present")
        if self.repo.get_run(run_id) is None:
            raise NotFound(f"run {run_id} not found")
        rows = self.repo.trades(run_id, symbol=symbol, limit=limit, offset=offset)
        return {
            "run_id": run_id, "symbol": symbol,
            "count": len(rows), "limit": limit, "offset": offset,
            "trades": [self._trade_row(r) for r in rows],
        }

    def by_symbol(self, run_id: int) -> dict:
        if not self.repo.tables_ready():
            raise Unavailable("backtest tables not present")
        if self.repo.get_run(run_id) is None:
            raise NotFound(f"run {run_id} not found")
        rows = self.repo.by_symbol(run_id)
        out = []
        for r in rows:
            decided = (r["wins"] or 0) + (r["losses"] or 0)
            wr = (r["wins"] / decided * 100) if decided else 0.0
            out.append({
                "symbol": r["symbol"],
                "trades": int(r["trades"]),
                "wins": int(r["wins"] or 0),
                "losses": int(r["losses"] or 0),
                "win_rate": round(wr, 1),
                "pnl_raw": round(float(r["pnl_raw"] or 0), 2),
                "pnl_leveraged": round(float(r["pnl_leveraged"] or 0), 2),
            })
        return {"run_id": run_id, "count": len(out), "by_symbol": out}

    # ---- helpers ----

    def _run_summary(self, r) -> dict:
        return {
            "id": int(r["id"]),
            "strategy": r["strategy"],
            "universe": r["universe"],
            "symbols_count": int(r["symbols_count"]),
            "start_date": r["start_date"],
            "end_date": r["end_date"],
            "leverage": float(r["leverage"]),
            "total_signals": int(r["total_signals"]),
            "total_trades": int(r["total_trades"]),
            "wins": int(r["wins"]),
            "losses": int(r["losses"]),
            "pnl_raw": round(float(r["pnl_raw"]), 2),
            "pnl_leveraged": round(float(r["pnl_leveraged"]), 2),
            "max_dd_raw": round(float(r["max_dd_raw"]), 2),
            "created_at": int(r["created_at"]),
            "notes": r["notes"],
        }

    def _trade_row(self, r) -> dict:
        return {
            "symbol": r["symbol"],
            "direction": r["direction"],
            "setup_ts": int(r["setup_ts"]),
            "entry_ts": int(r["entry_ts"]),
            "entry_price": float(r["entry_price"]),
            "sl": float(r["sl"]),
            "target": float(r["target"]),
            "exit_ts": int(r["exit_ts"]),
            "exit_price": float(r["exit_price"]),
            "exit_reason": r["exit_reason"],
            "qty": int(r["qty"]),
            "pnl_raw": round(float(r["pnl_raw"]), 2),
            "pnl_leveraged": round(float(r["pnl_leveraged"]), 2),
            "rr_at_entry": round(float(r["rr_at_entry"]), 2),
            "signal_tags": r["signal_tags"],
        }
