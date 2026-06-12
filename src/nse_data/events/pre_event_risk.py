"""Pre-event run-up risk (FEATURE_CHECKLIST Week 18, task 18.2).

Nightly job: for every stock with a pending result in the next
``_HORIZON_DAYS``, measure how much has ALREADY moved into the event
(close-to-close run over 5 and 10 sessions) and classify what's priced in:

    BUY_RUMOR_IN_PLAY    run > +8%      — rumor bought; in-line result can fade
    MILD_ANTICIPATION    +3% .. +8%
    NORMAL               −3% .. +3%
    MILD_FEAR            −8% .. −3%     — fills the checklist's −3..−8 gap
    FEAR_PRICED          −15% .. −8%    — bad news largely priced
    SELL_RUMOR_IN_PLAY   run < −15%     — heavy distribution into the print

The classification keys off the 10-day run (the 19.2 BUY_RUMOR psych state
reads the same number), falling back to the 5-day run when 10 daily bars
aren't on file yet.

Results land on `indicator_live` (pre_event_run_5d/10d, days_to_event,
pre_event_state) and are mirrored into the `ind:{symbol}` Redis hash, where
the dispatcher's buy-rumor gate (18.3) and the psychology classifier (19.2)
read them. Symbols whose event has passed/expired are cleared each run so a
stale BUY_RUMOR_IN_PLAY can't keep suppressing longs after the result.

    run_pre_event_pass(conn, redis_client)        # measure + classify + persist
    register_pre_event_risk_job(scheduler, path)  # nightly 20:20 IST
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

import structlog

from .calendar import RESULT_EVENT, _parse_nse_date
from .pre_screen import _run_up

log = structlog.get_logger()

_HORIZON_DAYS = 10

# (threshold, label): first band whose threshold the run meets, scanning down.
_PRE_EVENT_BANDS = (
    (8.0, "BUY_RUMOR_IN_PLAY"),
    (3.0, "MILD_ANTICIPATION"),
    (-3.0, "NORMAL"),
    (-8.0, "MILD_FEAR"),
    (-15.0, "FEAR_PRICED"),
)
_PRE_EVENT_FLOOR = "SELL_RUMOR_IN_PLAY"

# Hash fields mirrored to Redis ind:{symbol} (hset preserves the live job's
# other fields; the live job's own flush likewise preserves these).
_REDIS_FIELDS = ("pre_event_run_5d", "pre_event_run_10d",
                 "days_to_event", "pre_event_state")


def classify_pre_event_run(run_pct: float | None) -> str | None:
    """Bucket a pre-event run % into the 18.2 taxonomy (None when unmeasurable)."""
    if run_pct is None:
        return None
    for threshold, label in _PRE_EVENT_BANDS:
        if run_pct >= threshold:
            return label
    return _PRE_EVENT_FLOOR


def upcoming_result_events(
    conn: sqlite3.Connection, today: _dt.date, *, horizon_days: int = _HORIZON_DAYS,
) -> dict[str, int]:
    """{symbol: days_to_event} for the NEAREST upcoming result per symbol."""
    horizon = (today + _dt.timedelta(days=horizon_days)).isoformat()
    rows = conn.execute(
        "SELECT symbol, MIN(expected_date) FROM pending_events "
        "WHERE event_type=? AND status='upcoming' "
        "AND expected_date >= ? AND expected_date <= ? GROUP BY symbol",
        (RESULT_EVENT, today.isoformat(), horizon),
    ).fetchall()
    out: dict[str, int] = {}
    for symbol, expected_date in rows:
        d = _parse_nse_date(expected_date)
        if d is not None:
            out[symbol] = (d - today).days
    return out


def _upsert_live(conn: sqlite3.Connection, symbol: str, values: dict) -> None:
    """Write the pre-event columns onto indicator_live without disturbing the
    live job's columns (UPSERT on symbol; a missing row gets created so the
    nightly run still lands for symbols the live job hasn't touched yet)."""
    cols = ("pre_event_run_5d", "pre_event_run_10d", "days_to_event", "pre_event_state")
    updates = ",".join(f"{c}=excluded.{c}" for c in cols)
    conn.execute(
        f"INSERT INTO indicator_live (symbol, updated_at, {','.join(cols)}) "
        f"VALUES (?, ?, ?, ?, ?, ?) "
        f"ON CONFLICT(symbol) DO UPDATE SET {updates}",
        (symbol, values["updated_at"], *(values[c] for c in cols)),
    )


def _mirror_to_redis(redis_client, symbol: str, values: dict) -> None:
    if redis_client is None:
        return
    try:
        mapping = {f: ("" if values.get(f) is None else str(values[f]))
                   for f in _REDIS_FIELDS}
        redis_client.hset(f"ind:{symbol}", mapping=mapping)
    except Exception:  # noqa: BLE001 — Redis mirror is best-effort
        pass


def run_pre_event_pass(
    conn: sqlite3.Connection,
    *,
    redis_client=None,
    now: _dt.datetime | None = None,
    horizon_days: int = _HORIZON_DAYS,
) -> dict:
    """Measure + classify the pre-event run for every upcoming reporter.

    Also clears the pre-event columns for symbols that no longer have an
    upcoming event, so yesterday's BUY_RUMOR_IN_PLAY can't outlive the result.
    """
    from nse_data.scheduler import market_hours

    now = now or market_hours.now_ist()
    today = now.date()
    events = upcoming_result_events(conn, today, horizon_days=horizon_days)

    # Clear stale rows first (symbols whose event passed / got reconciled away).
    placeholders = ",".join("?" * len(events)) or "''"
    stale = conn.execute(
        "SELECT symbol FROM indicator_live WHERE pre_event_state IS NOT NULL "
        f"AND symbol NOT IN ({placeholders})",
        tuple(events),
    ).fetchall()
    for (symbol,) in stale:
        conn.execute(
            "UPDATE indicator_live SET pre_event_run_5d=NULL, pre_event_run_10d=NULL, "
            "days_to_event=NULL, pre_event_state=NULL WHERE symbol=?",
            (symbol,),
        )
        _mirror_to_redis(redis_client, symbol, {})

    classified: dict[str, int] = {}
    for symbol, days_to_event in events.items():
        ru5, ru10 = _run_up(conn, symbol)
        state = classify_pre_event_run(ru10 if ru10 is not None else ru5)
        values = {
            "updated_at": now.isoformat(),
            "pre_event_run_5d": ru5,
            "pre_event_run_10d": ru10,
            "days_to_event": days_to_event,
            "pre_event_state": state,
        }
        _upsert_live(conn, symbol, values)
        _mirror_to_redis(redis_client, symbol, values)
        if state:
            classified[state] = classified.get(state, 0) + 1
    conn.commit()

    report = {"events": len(events), "cleared": len(stale), **classified}
    log.info("pre_event_risk_pass", **report)
    return report


def register_pre_event_risk_job(scheduler, db_path: str) -> str:
    """Nightly 20:20 IST (after the 20:00 calendar + 20:15 pre-screen)."""
    from apscheduler.triggers.cron import CronTrigger

    from nse_data.scheduler import market_hours
    from nse_data.storage.db import open_db

    job_id = "events_pre_event_risk"

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        conn = open_db(db_path)
        try:
            redis_client = _connect_redis()
            run_pre_event_pass(conn, redis_client=redis_client)
        except Exception:
            log.exception("pre_event_risk_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=20, minute=20, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True,
    )
    return job_id


def _connect_redis():
    try:
        import redis  # type: ignore

        client = redis.Redis(decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None
