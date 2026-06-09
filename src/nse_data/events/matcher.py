"""Fundamental surprise + earnings evidence (Phase 5, E3).

When a result has filed, compares the actual numbers (extracted_financials) to
the expectation baseline — the stock's own recent growth trend, and, when the
estimates layer exists (E4), consensus. Produces the ``earnings`` evidence dict
the confidence scorer reads, so a post-result reaction that *confirms* the
fundamental surprise scores higher than one that contradicts it (a likely fade).

    ev = build_earnings_evidence(conn, "TCS", reaction_direction="long")
    # -> {"surprise_sign": +1, "confirms_direction": True, "run_up_class": ...}
"""
from __future__ import annotations

import sqlite3

from . import consensus
from ..fundamentals.from_results import quarter_growth

# YoY growth thresholds for the fundamental-surprise sign (proxy for beat/miss
# in the absence of consensus). PAT is the headline the market reacts to.
_BEAT_PAT_YOY = 15.0
_MISS_PAT_YOY = -10.0


def classify_surprise(growth: dict) -> tuple[int, float]:
    """(sign, magnitude) of the fundamental surprise from YoY growth.

    sign: +1 beat / 0 in-line / -1 miss. magnitude: |PAT YoY %| (0 if unknown).
    """
    pat = growth.get("yoy_pat_pct")
    rev = growth.get("yoy_revenue_pct")
    ref = pat if pat is not None else rev
    if ref is None:
        return (0, 0.0)
    if ref >= _BEAT_PAT_YOY:
        return (1, abs(ref))
    if ref <= _MISS_PAT_YOY:
        return (-1, abs(ref))
    return (0, abs(ref))


def latest_period(conn: sqlite3.Connection, symbol: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(period_ending) FROM extracted_financials "
        "WHERE symbol=? AND scope='standalone'",
        (symbol,),
    ).fetchone()
    return row[0] if row and row[0] else None


def build_earnings_evidence(
    conn: sqlite3.Connection, symbol: str, *, reaction_direction: str,
) -> dict | None:
    """Earnings evidence for the confidence scorer, or None if no actuals yet.

    ``reaction_direction`` is the post-result price reaction ('long'/'short').
    ``confirms_direction`` is True when the fundamental surprise agrees with it.
    """
    period = latest_period(conn, symbol)
    if period is None:
        return None
    growth = quarter_growth(conn, symbol, period, "standalone")

    # Prefer a TRUE surprise (actual vs consensus estimate) when we have one;
    # otherwise fall back to the YoY-trend proxy.
    estimate = consensus.nearest_estimate(conn, symbol, period)
    if estimate is not None:
        actual = _actual_fields(conn, symbol, period)
        sign, magnitude = consensus.classify_estimate_surprise(actual, estimate)
        surprise_basis = "consensus"
    else:
        sign, magnitude = classify_surprise(growth)
        surprise_basis = "trend"

    setup = conn.execute(
        "SELECT run_up_class, expectation_proxy_score, implied_move_pct "
        "FROM earnings_setups WHERE symbol=? ORDER BY event_date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    run_up_class = setup[0] if setup else None
    proxy_score = setup[1] if setup else None
    implied_move = setup[2] if setup else None

    confirms = (sign > 0 and reaction_direction == "long") or (
        sign < 0 and reaction_direction == "short"
    )
    return {
        "surprise_sign": sign,
        "surprise_magnitude": magnitude,
        "surprise_basis": surprise_basis,         # 'consensus' (true) or 'trend' (proxy)
        "confirms_direction": confirms,
        "run_up_class": run_up_class,
        "proxy_score": proxy_score,
        "implied_move_pct": implied_move,
        "period_ending": period,
        "yoy_revenue_pct": growth.get("yoy_revenue_pct"),
        "yoy_pat_pct": growth.get("yoy_pat_pct"),
        "consensus_pat_est_cr": estimate.get("pat_est_cr") if estimate else None,
        "consensus_rev_est_cr": estimate.get("rev_est_cr") if estimate else None,
    }


def _actual_fields(conn: sqlite3.Connection, symbol: str, period: str) -> dict:
    """The actual extracted headline numbers for one quarter (standalone)."""
    row = conn.execute(
        "SELECT revenue_cr, pat_cr, eps_basic FROM extracted_financials "
        "WHERE symbol=? AND scope='standalone' AND period_ending=?",
        (symbol, period),
    ).fetchone()
    if not row:
        return {}
    return {"revenue_cr": row[0], "pat_cr": row[1], "eps_basic": row[2]}
