"""Nightly consensus-estimate fetch (P6 / Week 17.5 S8 — unblocked 2026-06).

Pulls live estimates (news previews via Bing RSS + LLM, Moneycontrol quarterly
forecast, Yahoo earningsTrend) for the symbols with a result expected in the
next ``_HORIZON_DAYS``, so the earnings engine measures a TRUE beat/miss
instead of the YoY-trend proxy (``matcher.py`` flips ``surprise_basis`` to
'consensus' the moment a row exists; manual broker numbers loaded via
scripts/load_consensus.py outrank everything — ``consensus.SOURCE_RANK``;
lookup merges sources field-wise, news being the automated NII/NIM path).

Scheduled 20:05 IST on trading days: after the results calendar refreshes
``pending_events`` (20:00) and before the pre-screen stamps estimates onto
``earnings_setups`` (20:15). Fetch volume is small by construction — only
names actually about to report — and each source degrades per-symbol.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

import structlog

from .estimate_scraper import fetch_and_ingest

log = structlog.get_logger()

_HORIZON_DAYS = 10


def upcoming_symbols(
    conn: sqlite3.Connection, *, days: int = _HORIZON_DAYS,
    today: _dt.date | None = None,
) -> list[str]:
    """Symbols with a result expected in the next ``days`` (pending_events,
    falling back to frozen earnings_setups rows)."""
    today = today or _dt.date.today()
    horizon = today + _dt.timedelta(days=days)
    syms: set[str] = set()
    for query, args in (
        ("SELECT DISTINCT symbol FROM pending_events WHERE status='upcoming' "
         "AND event_type='result' AND expected_date BETWEEN ? AND ?",
         (today.isoformat(), horizon.isoformat())),
        ("SELECT DISTINCT symbol FROM earnings_setups WHERE event_date BETWEEN ? AND ?",
         (today.isoformat(), horizon.isoformat())),
    ):
        try:
            syms.update(r[0] for r in conn.execute(query, args).fetchall())
        except sqlite3.OperationalError:
            continue   # table not present in this deployment — fine
    return sorted(syms)


def run_consensus_pass(
    conn: sqlite3.Connection,
    symbols: list[str] | None = None,
    *,
    sources: tuple[str, ...] = ("news", "moneycontrol", "yahoo"),
) -> dict:
    """Fetch + ingest estimates for ``symbols`` (default: upcoming reporters)
    from each live source. Returns per-source ingest counts."""
    symbols = symbols if symbols is not None else upcoming_symbols(conn)
    report: dict = {"symbols": len(symbols)}
    if not symbols:
        return report
    for source in sources:
        try:
            if source == "news":
                from .consensus_sources import make_news_fetcher
                fetcher = make_news_fetcher(conn)
            elif source == "moneycontrol":
                from .consensus_sources import make_moneycontrol_fetcher
                fetcher = make_moneycontrol_fetcher()
            elif source == "yahoo":
                from .consensus_sources import make_yahoo_fetcher
                fetcher = make_yahoo_fetcher()
            else:
                continue
            report[source] = fetch_and_ingest(conn, symbols, fetcher=fetcher, source=source)
        except Exception:  # noqa: BLE001 — one source down ≠ no consensus tonight
            log.exception("consensus_source_failed", source=source)
            report[source] = 0
    log.info("consensus_pass", **report)
    return report


def register_consensus_job(scheduler, db_path: str) -> str:
    """Nightly 20:05 IST on trading days (between calendar and pre-screen)."""
    from apscheduler.triggers.cron import CronTrigger

    from nse_data.scheduler import market_hours
    from nse_data.storage.db import open_db

    job_id = "consensus_estimates"

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        conn = open_db(db_path)
        try:
            run_consensus_pass(conn)
        except Exception:
            log.exception("consensus_job_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=20, minute=5, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True,
    )
    return job_id
