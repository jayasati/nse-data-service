"""Earnings-engine read service — probability/accuracy + recent reactions.

Shapes repository rows into dashboard JSON. The odds (win-rate / avg return /
profit factor) reuse signals.earnings_odds — the exact metric the Telegram alert
shows — so the UI and the alerts can never disagree.
"""
from __future__ import annotations

from ...signals.earnings_odds import MIN_SAMPLES, compute_odds
from ..errors import Unavailable
from ..repositories.earnings import EarningsRepository


class EarningsService:
    def __init__(self, repo: EarningsRepository):
        self.repo = repo

    def odds(self) -> dict:
        """Overall + long + short win-rate/avg/PF, plus settled-sample coverage."""
        if not self.repo.tables_ready():
            raise Unavailable("signals/signal_outcomes not present (migrations not applied?)")
        conn = self.repo.conn
        cov = self.repo.coverage()
        return {
            "min_samples": MIN_SAMPLES,
            "coverage": {
                "total": int(cov["total"] or 0),
                "settled": int(cov["settled"] or 0),
                "longs": int(cov["longs"] or 0),
                "shorts": int(cov["shorts"] or 0),
            },
            "overall": compute_odds(conn),
            "long": compute_odds(conn, direction="long"),
            "short": compute_odds(conn, direction="short"),
        }

    def reactions(self, *, direction: str | None = None, limit: int = 100) -> dict:
        if not self.repo.tables_ready():
            raise Unavailable("signals/signal_outcomes not present")
        rows = self.repo.recent_reactions(direction=direction, limit=limit)
        return {
            "direction": direction, "count": len(rows),
            "reactions": [self._reaction(r) for r in rows],
        }

    def upcoming(self, *, limit: int = 100) -> dict:
        if not self.repo.tables_ready():
            raise Unavailable("signals/signal_outcomes not present")
        rows = self.repo.upcoming(limit=limit)
        return {"count": len(rows), "upcoming": [self._setup(r) for r in rows]}

    # ---- helpers ----

    def _reaction(self, r) -> dict:
        d = r["direction"]
        ret_1d = _f(r["ret_1d"])
        # direction-adjusted T+1 outcome: a short wins when price falls
        adj = None if ret_1d is None else (ret_1d if d == "long" else -ret_1d)
        return {
            "symbol": r["symbol"],
            "detected_at": r["detected_at"],
            "direction": d,
            "price": _f(r["price"]),
            "reaction_move_pct": _f(r["price_change_pct"]),
            "confidence": _f(r["confidence"]),
            "ret_1d": ret_1d,
            "ret_3d": _f(r["ret_3d"]),
            "outcome_pct": None if adj is None else round(adj, 2),
            "win": None if adj is None else adj > 0,
            "hit_t1": r["hit_t1"],
        }

    def _setup(self, r) -> dict:
        return {
            "symbol": r["symbol"],
            "event_date": r["event_date"],
            "run_up_5d": _f(r["run_up_5d"]),
            "run_up_class": r["run_up_class"],
            "implied_move_pct": _f(r["implied_move_pct"]),
            "pcr": _f(r["pcr"]),
            "fundamental_class": r["fundamental_class"],
            "expectation_proxy_score": _f(r["expectation_proxy_score"]),
            "consensus_rev_est": _f(r["consensus_rev_est"]),
            "flagged": r["flagged_at"] is not None,
        }


def _f(value) -> float | None:
    return None if value is None else round(float(value), 2)
