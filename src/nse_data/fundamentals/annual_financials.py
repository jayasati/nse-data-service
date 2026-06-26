"""Build the annual_financials table from audited integrated filings.

Quarterly extracted_financials can't power a Piotroski F (no cash flow, no prior-year). This fetches
the audited-annual XBRL filings (raw_integrated_filings), parses each with parse_annual (full-year
P&L + CFO + year-end balance sheet), and stores one row per (symbol, scope, fiscal-year). Stacking
years gives the prior-year comparison the F-score needs. Idempotent on (symbol, scope, fy_ending).
"""
from __future__ import annotations

import sqlite3
import time

import httpx
import structlog

from ..parsers.xbrl_financials import parse_annual
from ..scheduler import market_hours
from ..storage.db import open_db

log = structlog.get_logger(__name__)

_FIELDS = ("pat_cr", "pbt_cr", "finance_cost_cr", "cfo_cr", "revenue_cr", "cost_of_materials_cr",
           "total_assets_cr", "current_assets_cr", "current_liabilities_cr", "total_liabilities_cr",
           "borrowings_cr", "equity_cr", "eps_basic")
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _pending_filings(conn, symbols=None) -> list[tuple]:
    """Audited financials filings to parse — latest consolidated>standalone per (symbol, fy)."""
    rows = conn.execute(
        "SELECT symbol, qe_date, consolidated, xbrl_url FROM raw_integrated_filings "
        "WHERE filing_type='Integrated Filing- Financials' AND audited='Audited' "
        "AND xbrl_url IS NOT NULL AND qe_date IS NOT NULL").fetchall()
    if symbols is not None:
        sset = set(symbols)
        rows = [r for r in rows if r[0] in sset]
    # one filing per (symbol, fy): prefer consolidated
    best: dict[tuple, tuple] = {}
    for sym, qe, cons, url in rows:
        key = (sym, qe)
        is_cons = str(cons).lower() in ("yes", "true", "1", "consolidated")
        if key not in best or (is_cons and not best[key][1]):
            best[key] = (sym, is_cons, url, qe)
    return list(best.values())


_INTEGRATED_PATH = "/api/integrated-filing-results"
_INTEGRATED_REF = ("https://www.nseindia.com/companies-listing/"
                   "corporate-filings-integrated-filing")
# FY2025 audited annuals are filed within ~60d of the 31-Mar-2025 year-end; these windows (on the
# 500-row-capped API) cover that season. Historical prior-year basis for the Piotroski.
FY2025_WINDOWS = [("01-04-2025", "15-05-2025"), ("16-05-2025", "31-05-2025"),
                  ("01-06-2025", "20-06-2025"), ("21-06-2025", "15-07-2025")]


def fetch_api_filings(sm, windows, qe_date: str) -> list[tuple]:
    """Scan integrated-filing API date windows → [(symbol, is_consolidated, xbrl_url, qe)] for the
    given period-end, audited, deduped per symbol (prefer consolidated). For historical backfill."""
    best: dict[str, tuple] = {}
    for fd, td in windows:
        try:
            r = sm.get_json("integrated_filings", _INTEGRATED_PATH, _INTEGRATED_REF,
                            {"type": "Integrated Filing- Financials", "size": "500",
                             "from_date": fd, "to_date": td})
        except Exception:  # noqa: BLE001
            continue
        for d in (r.get("data") if isinstance(r, dict) else r) or []:
            if not isinstance(d, dict) or str(d.get("qe_Date")) != qe_date:
                continue
            if str(d.get("audited")).lower() != "audited":
                continue
            sym, url = (d.get("symbol") or "").strip(), d.get("xbrl")
            if not sym or not url:
                continue
            is_cons = str(d.get("consolidated")).lower() in ("yes", "true", "1", "consolidated")
            if sym not in best or (is_cons and not best[sym][1]):
                best[sym] = (sym, is_cons, url, qe_date)
    return list(best.values())


def build(conn: sqlite3.Connection, symbols=None, *, throttle: float = 0.4, limit=None,
          filings=None) -> dict:
    if filings is None:
        filings = _pending_filings(conn, symbols)
    if limit:
        filings = filings[:limit]
    written = failed = empty = 0
    with httpx.Client(timeout=30, follow_redirects=True, headers=_UA) as client:
        for sym, _cons, url, _qe in filings:
            try:
                rec = parse_annual(client.get(url).content)
            except Exception:  # noqa: BLE001
                failed += 1
                continue
            if not rec or not rec.get("period_ending"):
                empty += 1
                continue
            f = rec["fields"]
            conn.execute(
                f"INSERT OR REPLACE INTO annual_financials (symbol, scope, fy_ending, "
                f"{','.join(_FIELDS)}, xbrl_url, captured_at) "
                f"VALUES (?,?,?,{','.join('?' * len(_FIELDS))},?,datetime('now'))",
                (sym, rec["scope"], rec["period_ending"], *[f.get(k) for k in _FIELDS], url))
            conn.commit()                       # per-row: short locks, partial progress saved
            written += 1
            if throttle:
                time.sleep(throttle)
    rep = {"filings": len(filings), "written": written, "empty": empty, "failed": failed}
    log.info("annual_financials_build", **rep)
    return rep


def register_annual_financials_job(scheduler, db_path: str) -> str:
    """Nightly 21:40 IST — parse audited annual filings broadcast recently into annual_financials."""
    from apscheduler.triggers.cron import CronTrigger

    from ..events.calendar import _feature_enabled
    job_id = "annual_financials"

    def _tick():
        if not _feature_enabled("annual_financials", True):
            return
        conn = open_db(db_path)
        try:
            # only symbols with an audited filing broadcast in the last ~10 days (fresh results)
            syms = [r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM raw_integrated_filings WHERE filing_type="
                "'Integrated Filing- Financials' AND audited='Audited' "
                "AND broadcast_dt >= datetime('now','-10 day')")]
            if syms:
                build(conn, syms)
        except Exception:
            log.exception("annual_financials_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=21, minute=40, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True)
    return job_id
