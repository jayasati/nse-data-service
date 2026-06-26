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
import pathlib as _pathlib
import re
import sqlite3
import time

import structlog

log = structlog.get_logger()


def _feature_enabled(name: str, default: bool = True) -> bool:
    """Read an on/off toggle from config/collectors.yaml (missing file/key → default)."""
    try:
        import yaml
        path = _pathlib.Path(__file__).resolve().parents[3] / "config" / "collectors.yaml"
        if not path.exists():
            return default
        cfg = yaml.safe_load(path.read_text()) or {}
        return bool((cfg.get("features") or {}).get(name, default))
    except Exception:
        return default

RESULT_EVENT = "result"
_RESULTS_PURPOSE_RE = re.compile(r"financial result|quarterly result|unaudited|audited result", re.I)
_GRACE_DAYS = 3          # how long after expected_date before we call it 'expired'
_CADENCE_DAYS = 91       # ~one quarter; for the no-board-meeting fallback


def _parse_nse_date(s: str | None) -> _dt.date | None:
    """Parse NSE date strings ('01-May-2026', '01-May-2026 17:00:00',
    '01-May-2026 17:00' — raw_financial_results.filing_date drops the
    seconds — and ISO)."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(s[:len(fmt) + 4].strip(), fmt).date()
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
    event_type: str = RESULT_EVENT,
) -> None:
    # Don't clobber a more authoritative existing row (board_meeting > cadence > manual override
    # only when manual is absent) or a status already advanced past 'upcoming'.
    existing = conn.execute(
        "SELECT source, status FROM pending_events "
        "WHERE symbol=? AND expected_date=? AND event_type=?",
        (symbol, expected_date, event_type),
    ).fetchone()
    if existing is not None:
        # a confirmed manual override or a board meeting beats a low-confidence cadence guess
        if existing[1] != "upcoming" or (existing[0] in ("board_meeting", "manual")
                                         and source == "cadence"):
            return
    conn.execute(
        "INSERT OR REPLACE INTO pending_events "
        "(symbol, event_type, expected_date, source, confidence, status, purpose, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'upcoming', ?, ?, ?)",
        (symbol, event_type, expected_date, source, confidence, purpose, now, now),
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


# ── Non-results catalysts (Task 4: broaden beyond earnings) ───────────────────────────
# Map a board-meeting purpose token to a tradeable event_type. Results are handled
# separately (is_results_meeting) and excluded here.
_PURPOSE_RULES: tuple[tuple[str, str], ...] = (
    (r"fund\s*rais|preferential|qip|\bncd\b|debenture", "fund_raise"),
    (r"buy\s*back|buyback", "buyback"),
    (r"\bbonus\b", "bonus"),
    (r"stock\s*split|sub-?division|split", "split"),
    (r"\bdividend\b", "dividend"),
    (r"amalgamation|merger|de-?merger|scheme of arrangement|restructur", "restructure"),
    (r"delisting", "delisting"),
)
_CA_RULES: tuple[tuple[str, str], ...] = (
    (r"dividend|distribution", "dividend_ex"),
    (r"\bbonus\b", "bonus_ex"),
    (r"split|sub-?division", "split_ex"),
    (r"\brights\b", "rights_ex"),
    (r"buy\s*back|buyback", "buyback_ex"),
)


def classify_purpose(purpose: str | None, rules=_PURPOSE_RULES) -> list[str]:
    """Non-results event_types implied by a board-meeting purpose (may be several)."""
    p = (purpose or "").lower()
    return [etype for pat, etype in rules if re.search(pat, p)]


def _config_dir() -> "_pathlib.Path":
    return _pathlib.Path(__file__).resolve().parents[3] / "config"


def build_other_board_events(conn: sqlite3.Connection, today: _dt.date, now: int) -> int:
    """Future board meetings carrying a non-results catalyst (dividend declaration, buyback,
    fund-raise, bonus, split, restructuring). One pending_events row per classified type."""
    added = 0
    for symbol, meeting_date, purpose in conn.execute(
            "SELECT symbol, meeting_date, purpose FROM raw_board_meetings"):
        types = classify_purpose(purpose)
        if not types:
            continue
        d = _parse_nse_date(meeting_date)
        if d is None or d < today:
            continue
        for etype in types:
            _upsert_event(conn, symbol=symbol, expected_date=d.isoformat(), source="board_meeting",
                          confidence=0.8, purpose=purpose, now=now, event_type=etype)
            added += 1
    conn.commit()
    return added


def build_corp_action_events(conn: sqlite3.Connection, today: _dt.date, now: int) -> int:
    """Ex-date catalysts (dividend/bonus/split/rights/buyback) from raw_corporate_actions."""
    added = 0
    for symbol, subject, ex_date in conn.execute(
            "SELECT symbol, subject, ex_date FROM raw_corporate_actions WHERE ex_date IS NOT NULL"):
        d = _parse_nse_date(ex_date)
        if d is None or d < today:
            continue
        sub = (subject or "").lower()
        etype = next((e for pat, e in _CA_RULES if re.search(pat, sub)), None)
        if etype is None:
            continue
        _upsert_event(conn, symbol=symbol, expected_date=d.isoformat(), source="corp_action",
                      confidence=0.95, purpose=subject, now=now, event_type=etype)
        added += 1
    conn.commit()
    return added


def build_from_overrides(conn: sqlite3.Connection, today: _dt.date, now: int) -> int:
    """Manually-curated scheduled catalysts (investor days, analyst meets, capital-markets days)
    that the structured feeds don't carry a clean forward date for. NO fabrication — only what a
    human entered in config/events_override.yaml. This is how TMCV's 23-Jun Investor Day gets known."""
    path = _config_dir() / "events_override.yaml"
    if not path.exists():
        return 0
    try:
        import yaml
        spec = yaml.safe_load(path.read_text()) or {}
    except Exception:
        log.exception("events_override_parse_failed")
        return 0
    added = 0
    for ev in (spec.get("events") or []):
        sym, ed, et = ev.get("symbol"), ev.get("event_date"), ev.get("event_type")
        if not (sym and ed and et):
            continue
        d = _parse_nse_date(str(ed))
        if d is None or d < today:
            continue
        conf = 0.95 if str(ev.get("confidence", "confirmed")).lower() == "confirmed" else 0.5
        _upsert_event(conn, symbol=sym, expected_date=d.isoformat(), source="manual",
                      confidence=conf, purpose=ev.get("title"), now=now, event_type=et)
        added += 1
    conn.commit()
    return added


def expire_non_results(conn: sqlite3.Connection, today: _dt.date, now: int) -> int:
    """Non-results events have no 'filed' notion — once the date passes (+grace), expire them."""
    cutoff = (today - _dt.timedelta(days=_GRACE_DAYS)).isoformat()
    cur = conn.execute(
        "UPDATE pending_events SET status='expired', updated_at=? "
        "WHERE status='upcoming' AND event_type != ? AND expected_date < ?",
        (now, RESULT_EVENT, cutoff))
    conn.commit()
    return cur.rowcount


def promote_pre_event(conn: sqlite3.Connection, today: _dt.date,
                      *, trading_days: int = 3) -> list[dict]:
    """Add stocks with a confirmed event in the next 1–`trading_days` TRADING days to the
    live watchlist tagged 'pre_event:<type>'. No conviction score is attached — the event
    DIRECTION is unknown until the content emerges; this is a flag for human review only."""
    from nse_data.scheduler import market_hours
    from nse_data.signals.watchlist import add_to_watchlist

    # window end = the Nth trading day ahead (skip weekends/holidays)
    d, seen = today, 0
    while seen < trading_days:
        d += _dt.timedelta(days=1)
        if market_hours.is_trading_day(d):
            seen += 1
    window_end = d.isoformat()
    rows = conn.execute(
        "SELECT symbol, event_type, expected_date, confidence FROM pending_events "
        "WHERE status='upcoming' AND expected_date > ? AND expected_date <= ? "
        "ORDER BY expected_date",
        (today.isoformat(), window_end)).fetchall()
    now_iso = market_hours.now_ist().isoformat()
    out = []
    for symbol, etype, ed, conf in rows:
        ed_date = _parse_nse_date(ed)
        expires_iso = (ed_date + _dt.timedelta(days=1)).isoformat() if ed_date else now_iso
        add_to_watchlist(conn, symbol, f"pre_event:{etype}", now_iso, expires_iso)
        out.append({"symbol": symbol, "event_type": etype, "expected_date": ed,
                    "confidence": conf})
    conn.commit()
    return out


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

    # Task 4 — broaden beyond earnings: non-results board catalysts, ex-dates, manual overrides.
    if _feature_enabled("events_extended", True):
        report["from_other_board"] = build_other_board_events(conn, today, ts)
        report["from_corp_actions"] = build_corp_action_events(conn, today, ts)
        report["from_overrides"] = build_from_overrides(conn, today, ts)
        report["non_result_expired"] = expire_non_results(conn, today, ts)

    # Pre-event watchlist promotion (flag-for-review; no conviction score attached).
    if _feature_enabled("pre_event_promotion", True):
        promoted = promote_pre_event(conn, today)
        report["pre_event_promoted"] = len(promoted)
        report["pre_event_symbols"] = [f"{e['symbol']}:{e['event_type']}" for e in promoted][:30]

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
