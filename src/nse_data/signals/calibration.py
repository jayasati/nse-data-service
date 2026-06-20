"""Confidence calibration (FEATURE_CHECKLIST Phase 8, Week 26, tasks 26.1–26.3).

Is a signal scored 0.75 actually winning ~75% of the time? This builds the reliability curve
— bucket predictions by confidence, compare the bucket's actual win rate to its midpoint —
and the Expected Calibration Error. Pure machinery: it runs over (predicted_prob, won) pairs
from `signal_outcomes` once that fills (the signal detector is currently disabled, so there's
no data yet; the report degrades to empty). The output flags systematically over/under-
confident buckets so a layer weight (confidence_engine) can be retuned.
"""
from __future__ import annotations

import sqlite3

_BUCKETS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01))


def reliability_curve(pairs: list[tuple[float, bool]], buckets=_BUCKETS) -> list[dict]:
    """Per-confidence-bucket actual win rate vs expected (the bucket midpoint)."""
    out = []
    for lo, hi in buckets:
        wins = [1 if w else 0 for p, w in pairs if lo <= p < hi]
        if not wins:
            continue
        actual = sum(wins) / len(wins)
        expected = (lo + min(hi, 1.0)) / 2
        out.append({"bucket": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": len(wins),
                    "actual_win": round(actual, 3), "expected": round(expected, 3),
                    "error": round(actual - expected, 3)})
    return out


def expected_calibration_error(pairs: list[tuple[float, bool]]) -> float | None:
    """Sample-weighted mean |actual − expected| across buckets. None if no data."""
    curve = reliability_curve(pairs)
    n = sum(b["n"] for b in curve)
    if not n:
        return None
    return round(sum(b["n"] * abs(b["error"]) for b in curve) / n, 4)


def miscalibrated_buckets(pairs: list[tuple[float, bool]], *, tol: float = 0.10) -> list[dict]:
    """Buckets whose actual win rate is off its midpoint by more than `tol` (retune candidates)."""
    return [b for b in reliability_curve(pairs) if abs(b["error"]) > tol and b["n"] >= 20]


def load_outcome_pairs(conn: sqlite3.Connection) -> list[tuple[float, bool]]:
    """(confidence, won) pairs from signals × signal_outcomes. Empty until the detector runs."""
    for tbl in ("signals", "signal_outcomes"):
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)).fetchone():
            return []
    try:
        rows = conn.execute(
            "SELECT s.confidence, o.ret_1d FROM signals s JOIN signal_outcomes o "
            "ON o.signal_id = s.id WHERE s.confidence IS NOT NULL AND o.ret_1d IS NOT NULL"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(c, r > 0) for c, r in rows]


def calibration_report(conn: sqlite3.Connection) -> dict:
    pairs = load_outcome_pairs(conn)
    return {"n": len(pairs), "ece": expected_calibration_error(pairs),
            "curve": reliability_curve(pairs), "miscalibrated": miscalibrated_buckets(pairs)}
