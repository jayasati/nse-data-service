"""Consensus-estimate layer (Phase 5, E4).

Optional enrichment: when an analyst consensus estimate exists for a quarter, the
earnings engine measures a TRUE beat/miss (actual vs estimate) instead of leaning
only on the YoY-trend proxy. Everything here is source-agnostic — estimates can
arrive from a scraper, a manual CSV, or an API; this module just stores, looks
them up, and turns (actual, estimate) into a surprise.

    ingest_estimates(conn, rows, source="manual")           # load from any source
    est = nearest_estimate(conn, "TCS", "2026-03-31")       # {'pat_est_cr': ..., ...}
    sign, mag = classify_estimate_surprise(actual, est)     # +1 beat / 0 / -1 miss
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import time

import structlog

log = structlog.get_logger()

# Beat/miss thresholds (% by which the actual must clear the estimate).
_BEAT_PCT = 3.0
_MISS_PCT = -3.0
# Estimate periods may be stated a few days off the actual quarter-end.
_PERIOD_TOL_DAYS = 25


def upsert_estimate(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    period_ending: str,
    rev_est_cr: float | None = None,
    pat_est_cr: float | None = None,
    eps_est: float | None = None,
    source: str,
    as_of: int | None = None,
) -> None:
    """Insert/replace one estimate row."""
    conn.execute(
        "INSERT OR REPLACE INTO consensus_estimates "
        "(symbol, period_ending, rev_est_cr, pat_est_cr, eps_est, source, as_of) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (symbol, period_ending, rev_est_cr, pat_est_cr, eps_est, source,
         as_of if as_of is not None else int(time.time())),
    )
    conn.commit()


def ingest_estimates(
    conn: sqlite3.Connection, rows: list[dict], *, source: str,
    as_of: int | None = None,
) -> int:
    """Batch-load estimates from any source. Each row needs ``symbol`` and
    ``period_ending``; ``rev_est_cr`` / ``pat_est_cr`` / ``eps_est`` are optional.
    Returns the number ingested (rows missing the keys are skipped)."""
    as_of = as_of if as_of is not None else int(time.time())
    n = 0
    for r in rows:
        if not r.get("symbol") or not r.get("period_ending"):
            continue
        upsert_estimate(
            conn, symbol=r["symbol"], period_ending=r["period_ending"],
            rev_est_cr=r.get("rev_est_cr"), pat_est_cr=r.get("pat_est_cr"),
            eps_est=r.get("eps_est"), source=source, as_of=as_of,
        )
        n += 1
    log.info("consensus_ingest", source=source, ingested=n)
    return n


def _parse_date(s: str) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def _row_to_dict(row) -> dict:
    return {
        "period_ending": row[0], "rev_est_cr": row[1], "pat_est_cr": row[2],
        "eps_est": row[3], "source": row[4], "as_of": row[5],
    }


def nearest_estimate(
    conn: sqlite3.Connection, symbol: str, period_ending: str,
    tol_days: int = _PERIOD_TOL_DAYS,
) -> dict | None:
    """The estimate for ``period_ending`` (exact, else within ±tol_days).

    When several sources have an estimate, the most recently captured (max as_of)
    wins. Returns a dict or None.
    """
    exact = conn.execute(
        "SELECT period_ending, rev_est_cr, pat_est_cr, eps_est, source, as_of "
        "FROM consensus_estimates WHERE symbol=? AND period_ending=? "
        "ORDER BY as_of DESC LIMIT 1",
        (symbol, period_ending),
    ).fetchone()
    if exact:
        return _row_to_dict(exact)

    target = _parse_date(period_ending)
    if target is None:
        return None
    rows = conn.execute(
        "SELECT period_ending, rev_est_cr, pat_est_cr, eps_est, source, as_of "
        "FROM consensus_estimates WHERE symbol=?",
        (symbol,),
    ).fetchall()
    best, best_gap = None, tol_days + 1
    for row in rows:
        d = _parse_date(row[0])
        if d is None:
            continue
        gap = abs((d - target).days)
        if gap <= tol_days and gap < best_gap:
            best, best_gap = row, gap
    return _row_to_dict(best) if best else None


def estimate_surprise(actual: float | None, estimate: float | None) -> float | None:
    """Percent by which the actual beat (+) or missed (−) the estimate."""
    if actual is None or estimate is None or estimate == 0:
        return None
    return round((actual - estimate) / abs(estimate) * 100.0, 2)


def classify_estimate_surprise(actual: dict, estimate: dict | None) -> tuple[int, float]:
    """(sign, magnitude) of a TRUE surprise from actual vs estimate.

    ``actual`` carries ``pat_cr`` / ``revenue_cr`` / ``eps_basic`` (the extractor
    field names). Prefers PAT, then revenue, then EPS. sign: +1 beat / 0 in-line
    / -1 miss; magnitude: |surprise %|. Returns (0, 0.0) if not computable.
    """
    if not estimate:
        return (0, 0.0)
    for actual_key, est_key in (
        ("pat_cr", "pat_est_cr"), ("revenue_cr", "rev_est_cr"), ("eps_basic", "eps_est"),
    ):
        surprise = estimate_surprise(actual.get(actual_key), estimate.get(est_key))
        if surprise is None:
            continue
        if surprise >= _BEAT_PCT:
            return (1, abs(surprise))
        if surprise <= _MISS_PCT:
            return (-1, abs(surprise))
        return (0, abs(surprise))
    return (0, 0.0)
