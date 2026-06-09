"""Historical odds for earnings-reaction signals (Phase 5, E5).

Turns the labeled outcomes of past ``earnings_direction`` signals into the
"this setup: win 64% | avg +2.1% | PF 1.8 (n=83)" line shown in the alert.

Odds are based on the **direction-adjusted T+1 return** (``signal_outcomes.ret_1d``
flipped for shorts): a long wins when the day-after close is higher, a short when
it's lower. ret_1d is filled from daily bhavcopy, so it's available both for live
signals (nightly labeler) and for backfilled historical reactions (where intraday
history is absent). Win-rate via the daily move is the same metric in both, which
keeps backfilled and live odds comparable.
"""
from __future__ import annotations

import sqlite3

# Don't show odds until there's enough history to mean anything.
MIN_SAMPLES = 20


def compute_odds(conn: sqlite3.Connection, *, direction: str | None = None) -> dict:
    """Win-rate / avg return / profit factor over past earnings reactions.

    ``direction`` filters to 'long' or 'short' setups (None = all). Returns
    ``{n, wins, win_rate, avg_return_pct, profit_factor}``; n is the count with a
    settled T+1 outcome.
    """
    sql = (
        "SELECT COALESCE(s.direction, 'long'), so.ret_1d "
        "FROM signals s JOIN signal_outcomes so ON so.signal_id = s.id "
        "WHERE s.signal_type = 'earnings_direction' AND so.ret_1d IS NOT NULL"
    )
    params: list = []
    if direction is not None:
        sql += " AND COALESCE(s.direction, 'long') = ?"
        params.append(direction)
    rows = conn.execute(sql, params).fetchall()

    adj = [r1 if d == "long" else -r1 for d, r1 in rows]
    n = len(adj)
    if n == 0:
        return {"n": 0, "wins": 0, "win_rate": None,
                "avg_return_pct": None, "profit_factor": None}

    wins = sum(1 for a in adj if a > 0)
    gains = sum(a for a in adj if a > 0)
    losses = sum(-a for a in adj if a < 0)
    return {
        "n": n,
        "wins": wins,
        "win_rate": round(wins / n * 100, 1),
        "avg_return_pct": round(sum(adj) / n, 2),
        "profit_factor": round(gains / losses, 2) if losses > 0 else None,
    }


def format_odds(odds: dict, *, min_samples: int = MIN_SAMPLES) -> str | None:
    """One-line odds string, or None when the sample is too thin to show."""
    if not odds or odds.get("n", 0) < min_samples or odds.get("win_rate") is None:
        return None
    pf = odds.get("profit_factor")
    pf_str = f" | PF {pf}" if pf is not None else ""
    return (f"📊 this setup: win {odds['win_rate']}% | "
            f"avg {odds['avg_return_pct']:+.1f}%{pf_str} (n={odds['n']})")
