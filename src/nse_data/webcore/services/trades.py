"""Paper-trade read service — shapes repository rows into dashboard JSON.

No SQL, no FastAPI. A "win" is a closed trade with net_pnl > 0; win/success
rate is over decided (closed) trades only. Open trades have no P&L yet and are
counted separately.
"""

from __future__ import annotations

from ..errors import Unavailable
from ..repositories.trades import TradesRepository


class TradesService:
    def __init__(self, repo: TradesRepository):
        self.repo = repo

    def overview(self) -> dict:
        if not self.repo.tables_ready():
            raise Unavailable("paper_trades table not present (migration 036 not applied?)")
        r = self.repo.overview()
        wins, losses = int(r["wins"] or 0), int(r["losses"] or 0)
        return {
            "total": int(r["total"] or 0),
            "open": int(r["open"] or 0),
            "closed": int(r["closed"] or 0),
            "wins": wins,
            "losses": losses,
            "win_rate": _win_rate(wins, losses),
            "net_pnl": round(float(r["net_pnl"] or 0.0), 2),
            "gross_pnl": round(float(r["gross_pnl"] or 0.0), 2),
        }

    def by_strategy(self) -> dict:
        if not self.repo.tables_ready():
            raise Unavailable("paper_trades table not present")
        out = []
        for r in self.repo.by_strategy():
            wins, losses = int(r["wins"] or 0), int(r["losses"] or 0)
            out.append({
                "strategy": r["signal_type"],
                "total": int(r["total"] or 0),
                "open": int(r["open"] or 0),
                "closed": int(r["closed"] or 0),
                "wins": wins,
                "losses": losses,
                "win_rate": _win_rate(wins, losses),
                "net_pnl": round(float(r["net_pnl"] or 0.0), 2),
                "avg_win": round(float(r["avg_win"]), 2) if r["avg_win"] is not None else None,
                "avg_loss": round(float(r["avg_loss"]), 2) if r["avg_loss"] is not None else None,
            })
        return {"count": len(out), "by_strategy": out}

    def list_trades(
        self, *,
        status: str | None = None,
        strategy: str | None = None,
        limit: int = 100, offset: int = 0,
    ) -> dict:
        if not self.repo.tables_ready():
            raise Unavailable("paper_trades table not present")
        rows = self.repo.list_trades(
            status=status, strategy=strategy, limit=limit, offset=offset,
        )
        return {
            "status": status, "strategy": strategy,
            "count": len(rows), "limit": limit, "offset": offset,
            "trades": [self._trade_row(r) for r in rows],
        }

    # ---- helpers ----

    def _trade_row(self, r) -> dict:
        return {
            "id": int(r["id"]),
            "signal_id": r["signal_id"],
            "symbol": r["symbol"],
            "strategy": r["signal_type"],
            "entry_price": _f(r["entry_price"]),
            "sl_price": _f(r["sl_price"]),
            "t1_price": _f(r["t1_price"]),
            "entry_time": r["entry_time"],
            "exit_price": _f(r["exit_price"]),
            "exit_time": r["exit_time"],
            "exit_reason": r["exit_reason"],
            "gross_pnl": _f(r["gross_pnl"]),
            "net_pnl": _f(r["net_pnl"]),
            "status": r["status"],
        }


def _win_rate(wins: int, losses: int) -> float:
    decided = wins + losses
    return round(wins / decided * 100, 1) if decided else 0.0


def _f(value) -> float | None:
    return None if value is None else round(float(value), 2)
