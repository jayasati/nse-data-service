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

# When several sources carry an estimate for the same quarter, accuracy order
# wins before recency: a hand-entered broker number beats LLM-read news
# previews beats Moneycontrol's aggregate beats Yahoo's (whose Indian coverage
# is thinnest). Unknown sources rank last.
SOURCE_RANK = {"manual": 0, "news": 1, "moneycontrol": 2, "yahoo": 3}
_DEFAULT_RANK = 9


def _rank(source: str | None) -> int:
    return SOURCE_RANK.get(source or "", _DEFAULT_RANK)


def upsert_estimate(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    period_ending: str,
    rev_est_cr: float | None = None,
    pat_est_cr: float | None = None,
    eps_est: float | None = None,
    nii_est_cr: float | None = None,
    nim_est_pct: float | None = None,
    source: str,
    as_of: int | None = None,
) -> None:
    """Insert/replace one estimate row."""
    conn.execute(
        "INSERT OR REPLACE INTO consensus_estimates "
        "(symbol, period_ending, rev_est_cr, pat_est_cr, eps_est, "
        " nii_est_cr, nim_est_pct, source, as_of) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (symbol, period_ending, rev_est_cr, pat_est_cr, eps_est,
         nii_est_cr, nim_est_pct, source,
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
            eps_est=r.get("eps_est"), nii_est_cr=r.get("nii_est_cr"),
            nim_est_pct=r.get("nim_est_pct"), source=source, as_of=as_of,
        )
        n += 1
    log.info("consensus_ingest", source=source, ingested=n)
    return n


def _parse_date(s: str) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


_EST_COLS = ("period_ending, rev_est_cr, pat_est_cr, eps_est, "
             "nii_est_cr, nim_est_pct, source, as_of")


def _row_to_dict(row) -> dict:
    return {
        "period_ending": row[0], "rev_est_cr": row[1], "pat_est_cr": row[2],
        "eps_est": row[3], "nii_est_cr": row[4], "nim_est_pct": row[5],
        "source": row[6], "as_of": row[7],
    }


# The value fields a lookup merges across sources.
_VALUE_KEYS = ("rev_est_cr", "pat_est_cr", "eps_est", "nii_est_cr", "nim_est_pct")


def nearest_estimate(
    conn: sqlite3.Connection, symbol: str, period_ending: str,
    tol_days: int = _PERIOD_TOL_DAYS,
) -> dict | None:
    """The estimate for ``period_ending`` (exact, else within ±tol_days).

    Sources are merged **field-wise** in accuracy order (``SOURCE_RANK``:
    manual → news → moneycontrol → yahoo, then recency): each field takes the
    best-ranked source that carries it — so a news row holding only NII never
    masks Moneycontrol's PAT, and a manual number always wins its field.
    ``source`` lists the contributors ('manual+moneycontrol'). Returns a dict
    or None."""
    target = _parse_date(period_ending)
    rows = conn.execute(
        f"SELECT {_EST_COLS} FROM consensus_estimates WHERE symbol=?",
        (symbol,),
    ).fetchall()

    def gap_of(row) -> int | None:
        if row[0] == period_ending:
            return 0
        d = _parse_date(row[0])
        if d is None or target is None:
            return None
        g = abs((d - target).days)
        return g if g <= tol_days else None

    eligible = [(g, row) for row in rows if (g := gap_of(row)) is not None]
    if not eligible:
        return None
    # One quarter only: the period closest to the target (ties → earlier date).
    chosen = min((g, row[0]) for g, row in eligible)[1]
    at = sorted((r for g, r in eligible if r[0] == chosen),
                key=lambda r: (_rank(r[6]), -(r[7] or 0)))

    merged: dict = {"period_ending": chosen, **{k: None for k in _VALUE_KEYS}}
    field_src: dict[str, str] = {}
    for row in at:                       # best-ranked first; first non-None wins
        d = _row_to_dict(row)
        for k in _VALUE_KEYS:
            if merged[k] is None and d[k] is not None:
                merged[k] = d[k]
                field_src[k] = d["source"]
    contributors = sorted(set(field_src.values()), key=_rank)
    merged["source"] = "+".join(contributors) if contributors else at[0][6]
    merged["as_of"] = max(r[7] or 0 for r in at)
    return merged


def estimates_by_source(
    conn: sqlite3.Connection, symbol: str, period_ending: str,
) -> list[dict]:
    """All sources' estimates for one quarter (exact period match), in
    SOURCE_RANK order — the cross-validation view: two independent sources
    within a few percent make the consensus trustworthy."""
    rows = conn.execute(
        f"SELECT {_EST_COLS} FROM consensus_estimates "
        "WHERE symbol=? AND period_ending=?",
        (symbol, period_ending),
    ).fetchall()
    return sorted((_row_to_dict(r) for r in rows),
                  key=lambda d: (_rank(d["source"]), -(d["as_of"] or 0)))


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
