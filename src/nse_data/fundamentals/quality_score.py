"""
Fundamental quality score (FEATURE_CHECKLIST Phase 4, Week 14).

A composite 0–100 score from data already in the DB — graded per component
(task 14.2), weighted, and normalised over **whatever components are available**
so a symbol with only P/E + market-cap still gets a score rather than a phantom 0.
`quality_score` is None when nothing is available (the caller then neither gates
nor nudges on it).

Sources: raw_fundamentals_screener (ROE/ROCE/D-E/PE/mcap/3y-sales-CAGR),
raw_quote_metadata (PE/mcap/sector — for the sector-relative PE),
raw_shareholding_pattern (promoter holding), and raw_shareholding_quarterly
(promoter PLEDGE % from the SHP XBRL — feeds the >25% deduction, mirroring the
Risk engine). Pledge stays None for symbols the SHP backfill hasn't filled yet.

Pure `compute_quality_score` is unit-tested; the readers + nightly job glue it
to SQLite.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..scheduler import market_hours
from ..storage.db import open_db

log = structlog.get_logger()
JOB_ID = "stock_fundamentals"

# Each component: (max points, weight). Score is the weighted average of
# component/max over available components, ×100.
_WEIGHTS = {
    "revenue": (10, 2.0),
    "roce": (10, 2.0),
    "roe": (10, 1.5),
    "de": (10, 1.5),
    "promoter": (8, 1.0),
    "pe": (5, 1.0),
}
_PLEDGE_DEDUCT = 15.0     # pledge > 25% (task 14.2); > 50% is a hard-gate kill


# ---- component grades (task 14.2) ------------------------------------------

def _grade_revenue(g: float | None) -> float | None:
    if g is None:
        return None
    if g > 15:
        return 10.0
    if g >= 10:
        return 7.0
    if g >= 5:
        return 4.0
    return 2.0 if g >= 0 else 0.0


def _grade_roce(v: float | None) -> float | None:
    if v is None:
        return None
    if v > 20:
        return 10.0
    if v >= 15:
        return 7.0
    if v >= 10:
        return 4.0
    return 1.0


def _grade_roe(v: float | None) -> float | None:
    if v is None:
        return None
    if v > 15:
        return 10.0
    if v >= 10:
        return 7.0
    if v >= 5:
        return 4.0
    return 1.0


def _grade_de(v: float | None) -> float | None:
    if v is None:
        return None
    if v < 0.3:
        return 10.0
    if v <= 1:
        return 7.0
    if v <= 2:
        return 3.0
    return 0.0


def _grade_promoter(v: float | None) -> float | None:
    if v is None:
        return None
    if v > 50:
        return 8.0
    if v >= 30:
        return 5.0
    return 2.0


def _grade_pe(pe: float | None, sector_avg: float | None) -> float | None:
    if pe is None or sector_avg is None or sector_avg <= 0:
        return None
    return 5.0 if pe < sector_avg else 2.0


# ---- composite -------------------------------------------------------------

def compute_quality_score(m: dict, sector_pe_avg: float | None = None) -> dict:
    """Grade → weight → normalise to 0–100 over available components.

    `m` keys: revenue_growth, roce, roe, de, promoter_holding, pe, pledge.
    Returns a dict with quality_score (or None) + loss_making / high_debt flags.
    """
    grades = {
        "revenue": _grade_revenue(m.get("revenue_growth")),
        "roce": _grade_roce(m.get("roce")),
        "roe": _grade_roe(m.get("roe")),
        "de": _grade_de(m.get("de")),
        "promoter": _grade_promoter(m.get("promoter_holding")),
        "pe": _grade_pe(m.get("pe"), sector_pe_avg),
    }

    num = den = 0.0
    for key, grade in grades.items():
        if grade is None:
            continue
        max_pts, weight = _WEIGHTS[key]
        num += weight * (grade / max_pts)
        den += weight

    score = None
    if den > 0:
        score = (num / den) * 100.0
        pledge = m.get("pledge")
        if pledge is not None and pledge > 25:
            score -= _PLEDGE_DEDUCT
        score = round(max(0.0, min(100.0, score)), 1)

    roe = m.get("roe")
    de = m.get("de")
    return {
        "quality_score": score,
        "loss_making": 1 if (roe is not None and roe < 0) else 0,
        "high_debt": 1 if (de is not None and de > 2) else 0,
    }


# ---- DB readers ------------------------------------------------------------

def _has(conn, name) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def sector_pe_averages(conn: sqlite3.Connection) -> dict[str, float]:
    """Average P/E per sector from quote metadata (positive PEs only)."""
    if not _has(conn, "raw_quote_metadata"):
        return {}
    rows = conn.execute(
        "SELECT sector, AVG(pe_ratio) FROM raw_quote_metadata "
        "WHERE sector IS NOT NULL AND pe_ratio > 0 GROUP BY sector"
    ).fetchall()
    return {s: avg for s, avg in rows if avg}


def gather_metrics(conn: sqlite3.Connection, symbol: str) -> tuple[dict, str | None]:
    """Collect raw fundamental inputs for `symbol`. Returns (metrics, sector)."""
    m: dict = {}
    sector = None

    if _has(conn, "raw_fundamentals_screener"):
        row = conn.execute(
            "SELECT roce, roe, stock_pe, market_cap, debt_to_equity, sales_cagr_3y "
            "FROM raw_fundamentals_screener WHERE symbol = ? "
            "ORDER BY as_of_date DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if row:
            m.update(roce=row[0], roe=row[1], pe=row[2], market_cap=row[3],
                     de=row[4], revenue_growth=row[5])

    if _has(conn, "raw_quote_metadata"):
        row = conn.execute(
            "SELECT pe_ratio, market_cap_cr, sector FROM raw_quote_metadata "
            "WHERE symbol = ?", (symbol,),
        ).fetchone()
        if row:
            sector = row[2]
            if m.get("pe") is None:
                m["pe"] = row[0]
            if m.get("market_cap") is None:
                m["market_cap"] = row[1]

    if _has(conn, "raw_shareholding_pattern"):
        row = conn.execute(
            "SELECT promoter_pct FROM raw_shareholding_pattern WHERE symbol = ? "
            "ORDER BY qe_date DESC LIMIT 1", (symbol,),
        ).fetchone()
        if row:
            m["promoter_holding"] = row[0]

    # Promoter pledge % from the SHP XBRL (same source the Risk engine reads).
    # Latest quarter with a non-null value; stays None until the backfill fills it.
    if _has(conn, "raw_shareholding_quarterly"):
        row = conn.execute(
            "SELECT promoter_pledge_pct FROM raw_shareholding_quarterly "
            "WHERE symbol = ? AND promoter_pledge_pct IS NOT NULL "
            "ORDER BY qe_date DESC LIMIT 1", (symbol,),
        ).fetchone()
        if row:
            m["pledge"] = row[0]

    m.setdefault("pledge", None)
    return m, sector


# ---- nightly job -----------------------------------------------------------

_COLUMNS = (
    "symbol", "quality_score", "revenue_growth_yoy", "roe", "roce", "debt_equity",
    "pe_ratio", "market_cap", "promoter_holding", "promoter_pledge",
    "loss_making", "high_debt", "updated_date",
)


def run_quality_pass(conn: sqlite3.Connection, symbols, *, now: datetime | None = None) -> dict:
    now = now or market_hours.now_ist()
    today = now.date().isoformat()
    sector_pe = sector_pe_averages(conn)
    placeholders = ",".join("?" * len(_COLUMNS))
    written = scored = 0
    for sym in symbols:
        m, sector = gather_metrics(conn, sym)
        result = compute_quality_score(m, sector_pe.get(sector or ""))
        row = {
            "symbol": sym, "quality_score": result["quality_score"],
            "revenue_growth_yoy": m.get("revenue_growth"), "roe": m.get("roe"),
            "roce": m.get("roce"), "debt_equity": m.get("de"), "pe_ratio": m.get("pe"),
            "market_cap": m.get("market_cap"), "promoter_holding": m.get("promoter_holding"),
            "promoter_pledge": m.get("pledge"), "loss_making": result["loss_making"],
            "high_debt": result["high_debt"], "updated_date": today,
        }
        conn.execute(
            f"INSERT OR REPLACE INTO stock_fundamentals ({','.join(_COLUMNS)}) "
            f"VALUES ({placeholders})",
            tuple(row[c] for c in _COLUMNS),
        )
        written += 1
        if result["quality_score"] is not None:
            scored += 1
    conn.commit()
    return {"symbols": written, "scored": scored}


def run_quality_job(db_path: str) -> dict:
    from ..indicators.universe import fno_plus_nifty500
    conn = open_db(db_path)
    try:
        return run_quality_pass(conn, fno_plus_nifty500(conn))
    finally:
        conn.close()


def register_quality_job(scheduler: BlockingScheduler, db_path: str) -> str:
    """Nightly 18:00 IST quality-score compute (task 14.3). Trading-day gated."""
    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        try:
            log.info("stock_fundamentals", **run_quality_job(db_path))
        except Exception:
            log.exception("stock_fundamentals_failed")

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=18, minute=0, timezone=market_hours.IST),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True,
    )
    return JOB_ID
