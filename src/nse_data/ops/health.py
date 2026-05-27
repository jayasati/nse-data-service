"""
Health computation for the ops dashboard.

This module has no web dependency — it turns the endpoints.yaml config plus the
live SQLite DB into a per-collector freshness verdict. api/routes/health.py
wraps it in FastAPI; tests exercise it directly.

The service keeps no run-history table, so "is collector X working?" is answered
indirectly: find the table it writes, read the newest row's timestamp, and
compare that to the most recent moment the collector's schedule + gate would
have allowed it to run (`last_expected_run`). If the data lags that moment by
more than a cadence-dependent tolerance, the feed is stale.

Status values:
    ok        - data is as fresh as the schedule allows
    stale     - data lags the last expected run (likely one or more missed runs)
    down      - data lags badly (service probably not running)
    empty     - table exists but has no rows yet
    no_table  - configured table doesn't exist (migration not applied)
    disabled  - endpoint has enabled: false
    unknown   - couldn't determine (no timestamp column, etc.)
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from ..scheduler import market_hours
from ..scheduler.market_hours import IST, MARKET_CLOSE, MARKET_OPEN, is_trading_day
# Generic table introspection lives in the shared read-core; re-exported here so
# `health.detect_ts_column` / `health._safe_ident` callers (and tests) keep working.
from ..webcore.introspect import detect_ts_column, table_exists, _safe_ident

# cadence token -> nominal interval in seconds.
CADENCE_SECONDS = {
    "15s": 15, "30s": 30,
    "1m": 60, "3m": 180, "5m": 300, "10m": 600, "30m": 1800,
    "1h": 3600, "daily": 86400, "weekly": 604800,
}

_INTRADAY = {"15s", "30s", "1m", "3m", "5m", "10m", "30m", "1h"}


def tolerance_seconds(cadence: str) -> int:
    """How far the newest row may lag `last_expected_run` before we warn.

    Intraday feeds tick every interval, so we allow ~2 missed ticks. Daily and
    weekly feeds get an absolute window sized to catch a single missed run
    while tolerating normal publish lateness (NSE posts EOD files hours late).
    """
    if cadence in ("daily",):
        return 6 * 3600
    if cadence in ("weekly",):
        return 36 * 3600
    interval = CADENCE_SECONDS.get(cadence, 3600)
    return 2 * interval + 120


def _parse_ts(value: Any) -> datetime | None:
    """Parse a stored timestamp into an IST-aware datetime, or None.

    Handles: Unix epoch seconds (int/float/numeric str), ISO dates/datetimes,
    and NSE's 'DD-Mon-YYYY' / 'DD-MON-YYYY' date strings. Date-only values are
    anchored at 23:59:59 IST so a same-day row reads as fresh against an
    earlier same-day run_at.
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=IST)

    s = str(value).strip()
    if not s:
        return None

    if s.isdigit():
        return datetime.fromtimestamp(int(s), tz=IST)

    # ISO first (date or datetime; may carry an offset).
    try:
        dt = datetime.fromisoformat(s)
        return _anchor(dt)
    except ValueError:
        pass

    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return _anchor(datetime.strptime(s, fmt))
        except ValueError:
            continue
    return None


def _anchor(dt: datetime) -> datetime:
    """Attach IST if naive; push date-only (midnight) values to end-of-day."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    else:
        dt = dt.astimezone(IST)
    if dt.time() == time(0, 0, 0):
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt


# ---------------------------------------------------------------------------
# DB freshness
# ---------------------------------------------------------------------------

def table_freshness(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    """Newest-row timestamp, row count, and detected ts column for one table."""
    if not table_exists(conn, table):
        return {"exists": False, "rows": 0, "last_ts": None, "ts_column": None}

    tbl = _safe_ident(table)
    rows = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    ts_col, _is_date = detect_ts_column(conn, table)

    last_ts = None
    if ts_col and rows:
        raw = conn.execute(
            f"SELECT MAX({_safe_ident(ts_col)}) FROM {tbl}"
        ).fetchone()[0]
        last_ts = _parse_ts(raw)

    return {"exists": True, "rows": rows, "last_ts": last_ts, "ts_column": ts_col}


# ---------------------------------------------------------------------------
# Expected-schedule reasoning
# ---------------------------------------------------------------------------

def _combine(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=IST)


def _parse_hhmm(s: str) -> time:
    h, m = s.strip().split(":")
    return time(int(h), int(m))


def _last_market_close_or_now(now: datetime) -> datetime:
    """Most recent instant the cash-session gate was open: now if mid-session,
    else the previous session's 15:30 close."""
    d = now.date()
    if is_trading_day(d) and now >= _combine(d, MARKET_OPEN):
        return min(now, _combine(d, MARKET_CLOSE))
    d -= timedelta(days=1)
    for _ in range(14):
        if is_trading_day(d):
            return _combine(d, MARKET_CLOSE)
        d -= timedelta(days=1)
    return _combine(now.date(), MARKET_CLOSE)


def _last_window(now: datetime, start: time, end: time, trading_only: bool) -> datetime:
    """Most recent instant inside a daily [start, end] active window."""
    def ok(dd: date) -> bool:
        return (not trading_only) or is_trading_day(dd)

    d = now.date()
    if ok(d) and now >= _combine(d, start):
        return min(now, _combine(d, end))
    d -= timedelta(days=1)
    for _ in range(14):
        if ok(d):
            return _combine(d, end)
        d -= timedelta(days=1)
    return _combine(now.date(), end)


def _scan_back(
    now: datetime, settle: int, times_for_date: Callable[[date], list[time]],
    lookback: int = 21,
) -> datetime | None:
    """Newest scheduled instant at or before (now - settle), scanning back day
    by day. `times_for_date` yields the fire times that apply on a given date."""
    cutoff = now - timedelta(seconds=settle)
    d = now.date()
    for back in range(lookback):
        dd = d - timedelta(days=back)
        cands = [_combine(dd, t) for t in times_for_date(dd)]
        cands = [c for c in cands if c <= cutoff]
        if cands:
            return max(cands)
    return None


_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def last_expected_run(cfg: dict, now: datetime) -> datetime | None:
    """The most recent wall-clock moment this collector's schedule + gate would
    have allowed it to run, at or before `now`. Returns None if undeterminable."""
    cadence = cfg.get("cadence")
    trading_only = bool(cfg.get("trading_day_only"))

    if cadence in _INTRADAY:
        if cfg.get("market_hours_only"):
            return _last_market_close_or_now(now)
        ah = cfg.get("active_hours")
        if ah:
            start_s, end_s = ah.split("-")
            return _last_window(now, _parse_hhmm(start_s), _parse_hhmm(end_s), trading_only)
        return now  # ungated interval

    if cadence == "daily":
        run_ats = _run_at_list(cfg.get("run_at"))
        times = [_parse_hhmm(t) for t in run_ats]

        def for_date(dd: date) -> list[time]:
            if trading_only and not is_trading_day(dd):
                return []
            return times

        return _scan_back(now, settle=1800, times_for_date=for_date)

    if cadence == "weekly":
        by_wd: dict[int, list[time]] = {}
        for tok in _run_at_list(cfg.get("run_at")):
            dow_s, hhmm = tok.split()
            by_wd.setdefault(_DOW[dow_s.strip().lower()], []).append(_parse_hhmm(hhmm))

        def for_date(dd: date) -> list[time]:
            return by_wd.get(dd.weekday(), [])

        return _scan_back(now, settle=7200, times_for_date=for_date)

    return None


def _run_at_list(run_at: Any) -> list[str]:
    if run_at is None:
        return []
    if isinstance(run_at, str):
        return [run_at]
    return [str(x) for x in run_at]


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def _classify(cfg: dict, fresh: dict, now: datetime) -> dict[str, Any]:
    if not cfg.get("enabled"):
        return {"status": "disabled", "lag_seconds": None}
    if not fresh["exists"]:
        return {"status": "no_table", "lag_seconds": None}
    if fresh["rows"] == 0:
        return {"status": "empty", "lag_seconds": None}

    last_ts = fresh["last_ts"]
    if last_ts is None:
        return {"status": "unknown", "lag_seconds": None}

    expected = last_expected_run(cfg, now)
    if expected is None:
        return {"status": "unknown", "lag_seconds": None}

    lag = (expected - last_ts).total_seconds()
    tol = tolerance_seconds(cfg.get("cadence", ""))
    if lag <= tol:
        status = "ok"
    elif lag <= 4 * tol:
        status = "stale"
    else:
        status = "down"
    return {"status": status, "lag_seconds": int(lag)}


def collector_health(
    conn: sqlite3.Connection, name: str, cfg: dict, now: datetime
) -> dict[str, Any]:
    table = cfg.get("table")
    fresh = (
        table_freshness(conn, table)
        if table
        else {"exists": False, "rows": 0, "last_ts": None, "ts_column": None}
    )
    verdict = _classify(cfg, fresh, now)
    last_ts = fresh["last_ts"]
    return {
        "name": name,
        "table": table,
        "cadence": cfg.get("cadence"),
        "enabled": bool(cfg.get("enabled")),
        "rows": fresh["rows"],
        "ts_column": fresh["ts_column"],
        "last_data": last_ts.isoformat() if last_ts else None,
        "data_age_seconds": int((now - last_ts).total_seconds()) if last_ts else None,
        "status": verdict["status"],
        "lag_seconds": verdict["lag_seconds"],
    }


_STATUS_ORDER = {
    "down": 0, "no_table": 1, "stale": 2, "empty": 3,
    "unknown": 4, "ok": 5, "disabled": 6,
}


def build_report(
    conn: sqlite3.Connection, endpoints: dict, now: datetime | None = None
) -> dict[str, Any]:
    """Full health report: per-collector verdicts plus a status summary.

    Collectors are sorted worst-first so the dashboard surfaces problems at the
    top without client-side sorting.
    """
    now = now or market_hours.now_ist()
    collectors = [
        collector_health(conn, name, cfg, now) for name, cfg in endpoints.items()
    ]
    collectors.sort(key=lambda c: (_STATUS_ORDER.get(c["status"], 9), c["name"]))

    summary: dict[str, int] = {}
    for c in collectors:
        summary[c["status"]] = summary.get(c["status"], 0) + 1

    newest = min(
        (c["data_age_seconds"] for c in collectors if c["data_age_seconds"] is not None),
        default=None,
    )

    return {
        "generated_at": now.isoformat(),
        "summary": summary,
        "total": len(collectors),
        "freshest_age_seconds": newest,  # smallest age across all feeds
        "collectors": collectors,
    }
