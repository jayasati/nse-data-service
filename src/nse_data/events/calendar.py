"""Results calendar — build pending_events (Phase 5, E2).

Nightly job that turns NSE's scheduled board meetings into a per-symbol
results calendar, so the pre-screen job knows which stocks have a result coming
and the post-result trigger knows to watch. Sources:
  * raw_board_meetings — meetings whose purpose mentions financial results
    (high confidence, exact date).
  * cadence fallback — symbols with prior quarterly filings but no scheduled
    meeting get a rough next-result estimate (~quarter after the last filing).

Status is reconciled each run: 'filed' once raw_financial_results shows a filing
on/after the expected date, 'expired' once the date has passed without one.

    run_calendar_pass(conn)                 # build + reconcile
    register_calendar_job(scheduler, path)  # nightly 20:00 IST
"""
from __future__ import annotations

import datetime as _dt
import re
import sqlite3
import time

import structlog

log = structlog.get_logger()

RESULT_EVENT = "result"
_RESULTS_PURPOSE_RE = re.compile(r"financial result|quarterly result|unaudited|audited result", re.I)
_GRACE_DAYS = 3          # how long after expected_date before we call it 'expired'
_CADENCE_DAYS = 91       # ~one quarter; for the no-board-meeting fallback


def _parse_nse_date(s: str | None) -> _dt.date | None:
    """Parse NSE date strings ('01-May-2026', '01-May-2026 17:00:00', ISO)."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(s[:len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    try:
        return _dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def is_results_meeting(purpose: str | None) -> bool:
    return bool(_RESULTS_PURPOSE_RE.search(purpose or ""))


def _upsert_event(
    conn: sqlite3.Connection, *, symbol: str, expected_date: str,
    source: str, confidence: float, purpose: str | None, now: int,
) -> None:
    # Don't clobber a more authoritative existing row (board_meeting > cadence)
    # or a status already advanced past 'upcoming'.
    existing = conn.execute(
        "SELECT source, status FROM pending_events "
        "WHERE symbol=? AND expected_date=? AND event_type=?",
        (symbol, expected_date, RESULT_EVENT),
    ).fetchone()
    if existing is not None:
        if existing[1] != "upcoming" or (existing[0] == "board_meeting" and source == "cadence"):
            return
    conn.execute(
        "INSERT OR REPLACE INTO pending_events "
        "(symbol, event_type, expected_date, source, confidence, status, purpose, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'upcoming', ?, ?, ?)",
        (symbol, RESULT_EVENT, expected_date, source, confidence, purpose, now, now),
    )


def build_from_board_meetings(conn: sqlite3.Connection, today: _dt.date, now: int) -> int:
    """Create 'upcoming' result events from future results board meetings."""
    rows = conn.execute(
        "SELECT symbol, meeting_date, purpose FROM raw_board_meetings"
    ).fetchall()
    added = 0
    for symbol, meeting_date, purpose in rows:
        if not is_results_meeting(purpose):
            continue
        d = _parse_nse_date(meeting_date)
        if d is None or d < today:
            continue
        _upsert_event(
            conn, symbol=symbol, expected_date=d.isoformat(),
            source="board_meeting", confidence=0.9, purpose=purpose, now=now,
        )
        added += 1
    conn.commit()
    return added


def build_cadence_fallback(conn: sqlite3.Connection, today: _dt.date, now: int) -> int:
    """For symbols with a recent quarterly filing but no scheduled meeting, add a
    rough next-result estimate ~one quarter after the last filing."""
    rows = conn.execute(
        "SELECT symbol, MAX(filing_date) FROM raw_financial_results "
        "WHERE period = 'Quarterly' GROUP BY symbol"
    ).fetchall()
    added = 0
    for symbol, last_filing in rows:
        d = _parse_nse_date(last_filing)
        if d is None:
            continue
        expected = d + _dt.timedelta(days=_CADENCE_DAYS)
        if expected < today:
            continue
        # Skip if a board-meeting event already exists for this symbol soon.
        has_bm = conn.execute(
            "SELECT 1 FROM pending_events WHERE symbol=? AND event_type=? "
            "AND source='board_meeting' AND status='upcoming'",
            (symbol, RESULT_EVENT),
        ).fetchone()
        if has_bm:
            continue
        _upsert_event(
            conn, symbol=symbol, expected_date=expected.isoformat(),
            source="cadence", confidence=0.4, purpose=None, now=now,
        )
        added += 1
    conn.commit()
    return added


def reconcile_status(conn: sqlite3.Connection, today: _dt.date, now: int) -> dict:
    """Mark events 'filed' (a result filing appeared) or 'expired' (date passed)."""
    upcoming = conn.execute(
        "SELECT symbol, expected_date FROM pending_events "
        "WHERE event_type=? AND status='upcoming'",
        (RESULT_EVENT,),
    ).fetchall()
    filed = expired = 0
    for symbol, expected_date in upcoming:
        ed = _parse_nse_date(expected_date)
        if ed is None:
            continue
        # filed: an actual quarterly filing on/after (expected - 5d)
        window_start = (ed - _dt.timedelta(days=5)).isoformat()
        rows = conn.execute(
            "SELECT filing_date FROM raw_financial_results "
            "WHERE symbol=? AND period='Quarterly'",
            (symbol,),
        ).fetchall()
        is_filed = any(
            (fd := _parse_nse_date(r[0])) is not None and fd.isoformat() >= window_start
            for r in rows
        )
        if is_filed:
            conn.execute(
                "UPDATE pending_events SET status='filed', updated_at=? "
                "WHERE symbol=? AND expected_date=? AND event_type=?",
                (now, symbol, expected_date, RESULT_EVENT),
            )
            filed += 1
        elif ed < today - _dt.timedelta(days=_GRACE_DAYS):
            conn.execute(
                "UPDATE pending_events SET status='expired', updated_at=? "
                "WHERE symbol=? AND expected_date=? AND event_type=?",
                (now, symbol, expected_date, RESULT_EVENT),
            )
            expired += 1
    conn.commit()
    return {"filed": filed, "expired": expired}


def run_calendar_pass(conn: sqlite3.Connection, *, now: _dt.datetime | None = None) -> dict:
    from nse_data.scheduler import market_hours

    now = now or market_hours.now_ist()
    today = now.date()
    ts = int(time.time())
    bm = build_from_board_meetings(conn, today, ts)
    cad = build_cadence_fallback(conn, today, ts)
    rec = reconcile_status(conn, today, ts)
    report = {"from_board_meetings": bm, "from_cadence": cad, **rec}
    log.info("calendar_pass", **report)
    return report


def register_calendar_job(scheduler, db_path: str) -> str:
    """Nightly 20:00 IST: rebuild the results calendar (trading-day gated)."""
    from apscheduler.triggers.cron import CronTrigger

    from nse_data.scheduler import market_hours
    from nse_data.storage.db import open_db

    job_id = "events_calendar"

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        conn = open_db(db_path)
        try:
            run_calendar_pass(conn)
        except Exception:
            log.exception("calendar_pass_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=20, minute=0, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True,
    )
    return job_id
