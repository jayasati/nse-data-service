"""
Sector radar (FEATURE_CHECKLIST Phase 2, Week 8, tasks 8.2/8.5).

Every 5 minutes during market hours, ranks the 11 NSE sectoral indices by their
strength relative to NIFTY 50 and writes one `sector_state` row per sector. The
confidence scorer (task 8.4) reads a signal's sector rank/trend from here.

Relative strength, robustly: the checklist defines rs_ratio = sector_return /
nifty_return, but that ratio explodes / flips sign whenever Nifty is near flat
(which it crosses constantly intraday). So **ranking is done on excess return**
(sector_pct − nifty_pct), which is always well-defined and is what "relative
strength" means here. rs_ratio is still stored for display, guarded to null when
Nifty is inside a small deadband.

All 11 indices have price data so all 11 are ranked. (Stock→sector mapping for
the confidence wiring is separate — see config/sector_mapping.yaml — and covers
only the sectors with constituent data.)

Registered from main.py via `register_sector_radar_job` (IntervalTrigger 300s,
market-hours gated), same pattern as the regime job.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..scheduler import market_hours
from ..scheduler.market_hours import is_market_open
from ..storage.db import open_db

log = structlog.get_logger()

JOB_ID = "market_sector_radar"
_INTERVAL_SECONDS = 300

NIFTY_BENCHMARK = "NIFTY 50"
SECTOR_INDICES = [
    "NIFTY BANK", "NIFTY IT", "NIFTY AUTO", "NIFTY PHARMA", "NIFTY FMCG",
    "NIFTY METAL", "NIFTY REALTY", "NIFTY ENERGY", "NIFTY INFRA",
    "NIFTY PSU BANK", "NIFTY MEDIA",
]

_RS_LOOKBACK_SECS = 30 * 60
_NIFTY_FLAT_PCT = 0.05      # |nifty %| below this -> rs_ratio is unreliable -> null
_TREND_DEADBAND = 0.05      # |Δ excess| below this (pct points) is 'flat'


# ============================================================================
# Pure relative-strength helpers
# ============================================================================

def excess_return(sector_pct: float | None, nifty_pct: float | None) -> float | None:
    """Sector return minus benchmark return — the stable RS measure used to rank."""
    if sector_pct is None or nifty_pct is None:
        return None
    return sector_pct - nifty_pct


def rs_ratio(sector_pct: float | None, nifty_pct: float | None) -> float | None:
    """sector/nifty for display — null when Nifty is too flat to divide by."""
    if sector_pct is None or nifty_pct is None or abs(nifty_pct) < _NIFTY_FLAT_PCT:
        return None
    return round(sector_pct / nifty_pct, 3)


def rank_by_excess(excess_by_sector: dict[str, float | None]) -> dict[str, int]:
    """Rank sectors 1 (best) .. N by excess return, descending.

    Sectors with no data sort last. Ties broken by name for determinism.
    """
    ordered = sorted(
        excess_by_sector.items(),
        key=lambda kv: (kv[1] is None, -(kv[1] or 0.0), kv[0]),
    )
    return {name: i + 1 for i, (name, _) in enumerate(ordered)}


def rs_trend(excess_now: float | None, excess_prior: float | None) -> str | None:
    """Is relative strength improving or fading vs ~30 min ago?"""
    if excess_now is None or excess_prior is None:
        return None
    delta = excess_now - excess_prior
    if delta > _TREND_DEADBAND:
        return "improving"
    if delta < -_TREND_DEADBAND:
        return "deteriorating"
    return "flat"


# ============================================================================
# DB readers
# ============================================================================

def _latest_pct(conn: sqlite3.Connection, index_symbol: str) -> tuple[int | None, float | None]:
    """(as_of, pct_change) of the newest row for an index."""
    row = conn.execute(
        "SELECT as_of, pct_change FROM raw_indices WHERE index_symbol = ? "
        "ORDER BY as_of DESC LIMIT 1",
        (index_symbol,),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _pct_at_or_before(conn: sqlite3.Connection, index_symbol: str, ts: int) -> float | None:
    """pct_change of the newest row at or before `ts` (for the ~30m-ago compare)."""
    row = conn.execute(
        "SELECT pct_change FROM raw_indices WHERE index_symbol = ? AND as_of <= ? "
        "ORDER BY as_of DESC LIMIT 1",
        (index_symbol, ts),
    ).fetchone()
    return row[0] if row else None


# ============================================================================
# Pass orchestration
# ============================================================================

def build_sector_states(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    """Compute a sector_state row for every sectoral index (not persisted)."""
    nifty_as_of, nifty_now = _latest_pct(conn, NIFTY_BENCHMARK)
    ref_ts = nifty_as_of if nifty_as_of is not None else 0
    nifty_prior = _pct_at_or_before(conn, NIFTY_BENCHMARK, ref_ts - _RS_LOOKBACK_SECS)

    sector_pct: dict[str, float | None] = {}
    excess_now: dict[str, float | None] = {}
    excess_prior: dict[str, float | None] = {}
    for name in SECTOR_INDICES:
        _, pct = _latest_pct(conn, name)
        sector_pct[name] = pct
        excess_now[name] = excess_return(pct, nifty_now)
        prior = _pct_at_or_before(conn, name, ref_ts - _RS_LOOKBACK_SECS)
        excess_prior[name] = excess_return(prior, nifty_prior)

    ranks = rank_by_excess(excess_now)

    rows = []
    for name in SECTOR_INDICES:
        rows.append({
            "sector_name": name,
            "as_of": now.isoformat(),
            "rs_ratio": rs_ratio(sector_pct[name], nifty_now),
            "rs_rank": ranks[name],
            "rs_trend": rs_trend(excess_now[name], excess_prior[name]),
            "volume_state": None,   # not derivable from index price feed; future work
            "sector_return_pct": sector_pct[name],
        })
    return rows


_COLUMNS = (
    "sector_name", "as_of", "rs_ratio", "rs_rank", "rs_trend",
    "volume_state", "sector_return_pct",
)


def _upsert(conn: sqlite3.Connection, rows: list[dict]) -> None:
    placeholders = ",".join("?" * len(_COLUMNS))
    conn.executemany(
        f"INSERT OR REPLACE INTO sector_state ({','.join(_COLUMNS)}) "
        f"VALUES ({placeholders})",
        [tuple(r[c] for c in _COLUMNS) for r in rows],
    )


def run_sector_pass(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict:
    """One ranking + upsert. Returns a small report (leader/laggard)."""
    now = now or market_hours.now_ist()
    rows = build_sector_states(conn, now)
    _upsert(conn, rows)
    conn.commit()
    by_rank = sorted(rows, key=lambda r: r["rs_rank"])
    return {
        "sectors": len(rows),
        "leader": by_rank[0]["sector_name"] if by_rank else None,
        "laggard": by_rank[-1]["sector_name"] if by_rank else None,
    }


# ============================================================================
# Read helper for the confidence scorer / dispatcher
# ============================================================================

def latest_sector_ranks(conn: sqlite3.Connection) -> dict[str, dict]:
    """Latest {sector_name: {'rs_rank', 'rs_trend'}} from the most recent snapshot.

    Tolerant of a pre-Week-8 DB (no sector_state table) -> empty dict.
    """
    try:
        newest = conn.execute("SELECT MAX(as_of) FROM sector_state").fetchone()[0]
    except sqlite3.OperationalError:
        return {}
    if not newest:
        return {}
    rows = conn.execute(
        "SELECT sector_name, rs_rank, rs_trend FROM sector_state WHERE as_of = ?",
        (newest,),
    ).fetchall()
    return {name: {"rs_rank": rank, "rs_trend": trend} for name, rank, trend in rows}


# ============================================================================
# Scheduling
# ============================================================================

def run_sector_job(db_path: str) -> dict:
    if not is_market_open():
        return {"skipped": "market_closed"}
    conn = open_db(db_path)
    try:
        return run_sector_pass(conn)
    finally:
        conn.close()


def register_sector_radar_job(scheduler: BlockingScheduler, db_path: str) -> str:
    """Attach the 5-minute sector-radar job (task 8.5). Market-hours gated."""
    def _tick():
        try:
            report = run_sector_job(db_path)
            if "skipped" not in report:
                log.info("market_sector_radar_tick", **report)
        except Exception:
            log.exception("market_sector_radar_failed")

    scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(seconds=_INTERVAL_SECONDS),
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return JOB_ID
