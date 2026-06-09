"""Persist extracted quarterly financials + compute growth (Phase 5, E1).

Wires the vision-first ``parsers.financial_extractor`` into production: finds
result-PDF announcements whose text is already extracted, runs the financial
extractor on the PDF, and stores the headline P&L numbers in
``extracted_financials`` (one row per scope). That table is the per-quarter
history the earnings engine reads to compute YoY/QoQ growth — the
fundamental-surprise input.

    from nse_data.storage.db import open_db
    conn = open_db("data/nse.db")
    run_extract_pass(conn, limit=20, use_llm=True)        # backfill
    quarter_growth(conn, "TCS", "2026-03-31")             # {'yoy_revenue_pct': 16.8, ...}
"""
from __future__ import annotations

import datetime as _dt
import re
import sqlite3
import time

import structlog

from nse_data.parsers.state import State

log = structlog.get_logger()

# Headline fields we persist, in column order. EPS is per-share rupees; the rest
# are crore. Mirrors financial_extractor's canonical names.
AMOUNT_FIELDS = (
    "revenue_cr", "other_income_cr", "total_income_cr", "total_expenses_cr",
    "pbt_cr", "tax_cr", "pat_cr", "total_comprehensive_income_cr",
    "eps_basic", "eps_diluted",
)

# A result filing's announcement subject. Board-meeting *outcomes* often carry
# the P&L too; if the extractor finds no table we simply store nothing for them.
_RESULT_SUBJECT_RE = re.compile(
    r"financial result|audited financial|unaudited financial|quarterly result|"
    r"outcome of board meeting",
    re.I,
)


def is_result_subject(subject: str | None) -> bool:
    return bool(_RESULT_SUBJECT_RE.search(subject or ""))


# --------------------------------------------------------------------------- #
# persistence
# --------------------------------------------------------------------------- #

def persist_extraction(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    period_ending: str,
    scope: str,
    fields: dict[str, float],
    units_phrase: str | None,
    confidence: float,
    strategy: str,
    source_fingerprint: str | None,
    broadcast_dt: str | None,
    relating_to: str | None = None,
    financial_year: str | None = None,
    now: int | None = None,
) -> None:
    """Upsert one (symbol, period_ending, scope) row into extracted_financials."""
    now = now if now is not None else int(time.time())
    cols = (
        "symbol", "period_ending", "scope", "relating_to", "financial_year",
        *AMOUNT_FIELDS,
        "units_phrase", "extract_confidence", "strategy",
        "source_fingerprint", "broadcast_dt", "extracted_at",
    )
    vals = [
        symbol, period_ending, scope, relating_to, financial_year,
        *[fields.get(f) for f in AMOUNT_FIELDS],
        units_phrase, confidence, strategy,
        source_fingerprint, broadcast_dt, now,
    ]
    placeholders = ", ".join(["?"] * len(cols))
    conn.execute(
        f"INSERT OR REPLACE INTO extracted_financials ({', '.join(cols)}) "
        f"VALUES ({placeholders})",
        vals,
    )
    conn.commit()


def _update_announcement(conn: sqlite3.Connection, fingerprint: str, fields: dict) -> None:
    """Update raw_announcements (same pattern as parsers/job.py:_update_row)."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE raw_announcements SET {set_clause} WHERE fingerprint = ?",
        list(fields.values()) + [fingerprint],
    )
    conn.commit()


def _state_for(stored: int, strategy: str, confidence: float) -> str:
    """Terminal pdf_status after a financial-extraction attempt."""
    if stored == 0:
        return State.EXTRACTION_FAILED
    if strategy == "vision":
        return State.EXTRACTED_VIA_VISION
    if confidence >= 0.75:
        return State.EXTRACTED
    return State.EXTRACTED_LOW_CONF


def extract_and_store(
    conn: sqlite3.Connection,
    *,
    fingerprint: str,
    symbol: str,
    subject: str | None,
    broadcast_dt: str | None,
    pdf_path: str,
    use_llm: bool = True,
    now: int | None = None,
) -> dict:
    """Run the financial extractor on one PDF and persist its numbers.

    Stores up to two rows (standalone + consolidated) when ``period_ending`` is
    known, and advances the announcement's pdf_status. Returns a summary dict.
    """
    now = now if now is not None else int(time.time())
    from nse_data.parsers.financial_extractor import extract

    res = extract(
        pdf_path, use_llm_fallback=use_llm,
        symbol=symbol, subject=subject, broadcast_dt=broadcast_dt,
    )

    stored = 0
    if res.period_ending:
        for scope, block in (("standalone", res.fields), ("consolidated", res.consolidated)):
            if block:
                persist_extraction(
                    conn, symbol=symbol, period_ending=res.period_ending, scope=scope,
                    fields=block, units_phrase=res.units_phrase, confidence=res.confidence,
                    strategy=res.strategy, source_fingerprint=fingerprint,
                    broadcast_dt=broadcast_dt, now=now,
                )
                stored += 1

    _update_announcement(conn, fingerprint, {
        "extraction_confidence": res.confidence,
        "extraction_strategy": res.strategy,
        "extraction_attempted_at": now,
        "pdf_status": _state_for(stored, res.strategy, res.confidence),
        "pdf_status_updated_at": now,
    })
    return {
        "stored": stored, "strategy": res.strategy, "confidence": res.confidence,
        "cost_usd": res.llm_cost_usd, "period_ending": res.period_ending,
    }


def run_extract_pass(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
    use_llm: bool = True,
    now: int | None = None,
) -> dict:
    """Backfill: extract+store financials for result PDFs not yet processed.

    Selects announcements at ``text_extracted`` whose subject looks like a result
    and that have no extracted_financials rows yet. Bounded by ``limit`` (each
    PDF is one LLM call, so keep batches modest against the daily spend cap).
    """
    candidates = conn.execute(
        "SELECT fingerprint, symbol, subject, broadcast_dt, pdf_path "
        "FROM raw_announcements "
        "WHERE pdf_status = ? AND pdf_path IS NOT NULL "
        "AND fingerprint NOT IN ("
        "  SELECT source_fingerprint FROM extracted_financials "
        "  WHERE source_fingerprint IS NOT NULL) "
        "ORDER BY broadcast_dt DESC",
        (State.TEXT_EXTRACTED,),
    ).fetchall()

    processed = stored = 0
    total_cost = 0.0
    report: list[dict] = []
    for fp, sym, subj, bdt, path in candidates:
        if processed >= limit:
            break
        if not is_result_subject(subj):
            continue
        r = extract_and_store(
            conn, fingerprint=fp, symbol=sym, subject=subj,
            broadcast_dt=bdt, pdf_path=path, use_llm=use_llm, now=now,
        )
        processed += 1
        stored += r["stored"]
        total_cost += r["cost_usd"]
        report.append({"symbol": sym, **r})
        log.info("extracted_financials", symbol=sym, **r)
    return {"processed": processed, "stored": stored, "cost_usd": total_cost, "rows": report}


# Per-night batch bound. Each PDF is one gpt-4o call; 40 keeps a heavy results
# night well under the LLMClient daily cap ($10). Unprocessed results carry over
# to the next night (the pass only picks text_extracted rows not yet stored).
EXTRACT_BATCH_LIMIT = 40

# Intraday cadence: results filed DURING market hours must have their financials
# extracted promptly so the post-result reaction's confidence can fold in the
# fundamental surprise within the dispatcher's ~15-min re-score window. Small
# per-tick batch (most ticks are no-ops — only new text_extracted results match).
INTRADAY_EXTRACT_INTERVAL_MIN = 5
INTRADAY_EXTRACT_BATCH = 10


def register_extract_runner(scheduler, db_path: str) -> str:
    """Nightly 21:00 IST: extract financials from the day's result PDFs.

    Runs after the calendar (20:00) / pre-screen (20:15) and late enough that the
    day's result PDFs have been downloaded + text-extracted. Trading-day gated;
    catch-up safe (a missed night's results are picked up the next run)."""
    from apscheduler.triggers.cron import CronTrigger

    from nse_data.scheduler import market_hours
    from nse_data.storage.db import open_db

    job_id = "extract_financials"

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        conn = open_db(db_path)
        try:
            report = run_extract_pass(conn, limit=EXTRACT_BATCH_LIMIT, use_llm=True)
            log.info("extract_financials_job", **report)
        except Exception:
            log.exception("extract_financials_job_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=21, minute=0, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True,
    )
    return job_id


def register_intraday_extract_runner(scheduler, db_path: str) -> str:
    """Every few minutes DURING market hours: extract financials from results
    that just filed, so a mid-session reaction can be scored with the actual
    fundamental surprise. Internally gated on market hours (off-hours = no-op);
    most ticks find nothing new and return immediately."""
    from apscheduler.triggers.interval import IntervalTrigger

    from nse_data.scheduler.market_hours import is_market_open
    from nse_data.storage.db import open_db

    job_id = "extract_financials_intraday"

    def _tick():
        if not is_market_open():
            return
        conn = open_db(db_path)
        try:
            report = run_extract_pass(conn, limit=INTRADAY_EXTRACT_BATCH, use_llm=True)
            if report["processed"]:
                log.info("extract_financials_intraday", **report)
        except Exception:
            log.exception("extract_financials_intraday_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=IntervalTrigger(minutes=INTRADAY_EXTRACT_INTERVAL_MIN),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True,
    )
    return job_id


# --------------------------------------------------------------------------- #
# growth (YoY / QoQ) over the stored history
# --------------------------------------------------------------------------- #

def _pct_change(current: float | None, prior: float | None) -> float | None:
    """Percent change, sign-aware. None if not computable (incl. prior == 0)."""
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / abs(prior) * 100.0


def _parse_date(s: str) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def _nearest_prior_row(
    conn: sqlite3.Connection, symbol: str, scope: str,
    current_period: str, months_back: int, tol_days: int = 25,
) -> sqlite3.Row | None:
    """The stored quarter closest to ``current_period`` shifted back N months.

    YoY uses months_back=12 (same quarter last year), QoQ uses 3 (prior quarter).
    Quarter-end days differ across quarters (Mar 31 vs Dec 31), so we match within
    a ±tol_days window rather than requiring an exact date.
    """
    cur = _parse_date(current_period)
    if cur is None:
        return None
    # shift back ~months_back months (30.44 days/month is plenty given tol window)
    target = cur - _dt.timedelta(days=round(months_back * 30.44))
    rows = conn.execute(
        "SELECT period_ending, revenue_cr, pat_cr, eps_basic, total_income_cr "
        "FROM extracted_financials "
        "WHERE symbol = ? AND scope = ? AND period_ending < ?",
        (symbol, scope, current_period),
    ).fetchall()
    best = None
    best_gap = tol_days + 1
    for row in rows:
        d = _parse_date(row[0])
        if d is None:
            continue
        gap = abs((d - target).days)
        if gap <= tol_days and gap < best_gap:
            best, best_gap = row, gap
    return best


def quarter_growth(
    conn: sqlite3.Connection, symbol: str, period_ending: str,
    scope: str = "standalone",
) -> dict:
    """YoY and QoQ growth for revenue / PAT / total_income at one quarter.

    Returns a dict with ``*_pct`` keys for whichever comparisons are computable
    from the stored history (empty when no prior quarter is on file).
    """
    cur = conn.execute(
        "SELECT revenue_cr, pat_cr, eps_basic, total_income_cr "
        "FROM extracted_financials "
        "WHERE symbol = ? AND scope = ? AND period_ending = ?",
        (symbol, scope, period_ending),
    ).fetchone()
    if cur is None:
        return {}
    rev, pat, _eps, tinc = cur

    out: dict[str, float] = {}
    yoy = _nearest_prior_row(conn, symbol, scope, period_ending, 12)
    if yoy is not None:
        for key, c, p in (
            ("yoy_revenue_pct", rev, yoy[1]),
            ("yoy_pat_pct", pat, yoy[2]),
            ("yoy_total_income_pct", tinc, yoy[4]),
        ):
            v = _pct_change(c, p)
            if v is not None:
                out[key] = round(v, 2)
    qoq = _nearest_prior_row(conn, symbol, scope, period_ending, 3)
    if qoq is not None:
        for key, c, p in (
            ("qoq_revenue_pct", rev, qoq[1]),
            ("qoq_pat_pct", pat, qoq[2]),
        ):
            v = _pct_change(c, p)
            if v is not None:
                out[key] = round(v, 2)
    return out
