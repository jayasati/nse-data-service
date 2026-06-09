"""Pre-event screening + flag alerts (Phase 5, E2).

For each result due in the next few days, freeze an ``earnings_setups`` snapshot
of what's already priced in (run-up, options-implied move, PCR, OI build-up,
sector context, recent growth) and, when the setup is notable, send a one-time
pre-event flag to the Telegram earnings topic ("buy-rumor priced in — caution").

This is the **pre-event** half of the engine. It places no trade; it warns. The
**post-event** trigger (E3) decides actual direction from the result-day
reaction, using this snapshot as the expectation baseline.

    run_pre_screen_pass(conn, horizon_days=3)        # compute + flag
    register_pre_screen_job(scheduler, db_path)      # nightly 20:15 IST
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import time

import structlog

from . import expectation
from .calendar import RESULT_EVENT, _parse_nse_date

log = structlog.get_logger()

_DEFAULT_HORIZON_DAYS = 3


# --------------------------------------------------------------------------- #
# measurements (DB-coupled)
# --------------------------------------------------------------------------- #

def _run_up(conn: sqlite3.Connection, symbol: str) -> tuple[float | None, float | None]:
    """(5d, 10d) close-to-close run-up % from daily bhavcopy."""
    from nse_data.indicators.ohlcv import read_ohlcv

    try:
        df = read_ohlcv(conn, symbol)
    except Exception:  # noqa: BLE001
        return (None, None)
    closes = df["close"].dropna() if not df.empty else df.get("close")
    if closes is None or len(closes) < 2:
        return (None, None)

    def _ru(n: int) -> float | None:
        if len(closes) < n + 1:
            return None
        last, prior = closes.iloc[-1], closes.iloc[-(n + 1)]
        if prior in (None, 0):
            return None
        return round((last - prior) / prior * 100.0, 2)

    return (_ru(5), _ru(10))


def _nearest_expiry(expiries: list[str], today: _dt.date) -> str | None:
    """The nearest expiry on/after today (NSE '26-May-2026' strings)."""
    dated = [(e, _parse_nse_date(e)) for e in expiries]
    future = sorted((d, e) for e, d in dated if d is not None and d >= today)
    return future[0][1] if future else None


def _options_snapshot(
    conn: sqlite3.Connection, symbol: str, today: _dt.date, days_to_event: float,
) -> dict:
    """ATM IV, implied move, and PCR from the latest option-chain snapshot."""
    out = {"iv_atm": None, "implied_move_pct": None, "pcr": None}
    as_of = conn.execute(
        "SELECT MAX(as_of) FROM raw_option_chain WHERE symbol=?", (symbol,)
    ).fetchone()
    if not as_of or as_of[0] is None:
        return out
    as_of = as_of[0]
    expiries = [r[0] for r in conn.execute(
        "SELECT DISTINCT expiry FROM raw_option_chain WHERE symbol=? AND as_of=?",
        (symbol, as_of),
    )]
    expiry = _nearest_expiry(expiries, today)
    if expiry is None:
        return out

    rows = conn.execute(
        "SELECT option_type, strike, implied_volatility, open_interest, underlying_value "
        "FROM raw_option_chain WHERE symbol=? AND as_of=? AND expiry=?",
        (symbol, as_of, expiry),
    ).fetchall()
    if not rows:
        return out

    spot = next((r[4] for r in rows if r[4] is not None), None)
    if spot:
        atm_strike = min((r[1] for r in rows), key=lambda s: abs(s - spot))
        ivs = [r[2] for r in rows if r[1] == atm_strike and r[2] not in (None, 0)]
        if ivs:
            out["iv_atm"] = round(sum(ivs) / len(ivs), 2)
            out["implied_move_pct"] = expectation.implied_move_from_iv(
                out["iv_atm"], max(days_to_event, 1.0)
            )
    pe_oi = sum(r[3] or 0 for r in rows if r[0] == "PE")
    ce_oi = sum(r[3] or 0 for r in rows if r[0] == "CE")
    if ce_oi > 0:
        out["pcr"] = round(pe_oi / ce_oi, 2)
    return out


def _sector(conn: sqlite3.Connection, symbol: str) -> tuple[int | None, str | None]:
    from nse_data.market.sector_map import sector_for

    sector = sector_for(symbol)
    if not sector:
        return (None, None)
    row = conn.execute(
        "SELECT rs_rank, rs_trend FROM sector_state WHERE sector_name=? "
        "ORDER BY as_of DESC LIMIT 1",
        (sector,),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _latest_growth(conn: sqlite3.Connection, symbol: str) -> dict:
    """YoY growth from the most recent stored quarter (standalone)."""
    from nse_data.fundamentals.from_results import quarter_growth

    row = conn.execute(
        "SELECT MAX(period_ending) FROM extracted_financials "
        "WHERE symbol=? AND scope='standalone'",
        (symbol,),
    ).fetchone()
    if not row or row[0] is None:
        return {}
    return quarter_growth(conn, symbol, row[0], "standalone")


# --------------------------------------------------------------------------- #
# snapshot + flag
# --------------------------------------------------------------------------- #

def _quarter_end_on_or_before(d: _dt.date) -> str:
    """Most recent quarter-end (Mar/Jun/Sep/Dec 31|30) on or before ``d``."""
    ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
    cands = [_dt.date(d.year, m, day) for m, day in ends if _dt.date(d.year, m, day) <= d]
    cands.append(_dt.date(d.year - 1, 12, 31))
    return max(cands).isoformat()


def _consensus_for_event(conn: sqlite3.Connection, symbol: str, event_date: str) -> dict | None:
    """Consensus estimate for the quarter this result reports on, if any."""
    from . import consensus

    ed = _parse_nse_date(event_date)
    if ed is None:
        return None
    return consensus.nearest_estimate(conn, symbol, _quarter_end_on_or_before(ed))


def build_setup(
    conn: sqlite3.Connection, symbol: str, event_date: str, today: _dt.date,
) -> dict:
    """Compute the full pre-event setup dict for one upcoming result."""
    ed = _parse_nse_date(event_date)
    days_to_event = max((ed - today).days, 1) if ed else 1

    ru5, ru10 = _run_up(conn, symbol)
    opts = _options_snapshot(conn, symbol, today, days_to_event)

    from nse_data.signals.compute import compute_oi_change
    oi_change_pct, _, _ = compute_oi_change(conn, symbol)
    oi_class = expectation.classify_oi_buildup(oi_change_pct, ru5)

    sector_rank, sector_trend = _sector(conn, symbol)
    growth = _latest_growth(conn, symbol)

    run_up_class = expectation.classify_runup(ru5)
    fundamental_class = expectation.classify_fundamental_trend(growth)
    proxy = expectation.expectation_proxy_score(run_up_class, fundamental_class, opts["pcr"])
    est = _consensus_for_event(conn, symbol, event_date)

    return {
        "symbol": symbol, "event_date": event_date,
        "run_up_5d": ru5, "run_up_10d": ru10, "run_up_class": run_up_class,
        "iv_atm": opts["iv_atm"], "implied_move_pct": opts["implied_move_pct"],
        "pcr": opts["pcr"], "oi_buildup_class": oi_class,
        "sector_rank": sector_rank, "sector_trend": sector_trend,
        "growth_yoy_rev": growth.get("yoy_revenue_pct"),
        "growth_yoy_pat": growth.get("yoy_pat_pct"),
        "fundamental_class": fundamental_class,
        "expectation_proxy_score": proxy,
        "consensus_rev_est": est.get("rev_est_cr") if est else None,
        "consensus_eps_est": est.get("eps_est") if est else None,
    }


def _upsert_setup(conn: sqlite3.Connection, setup: dict, now: int, flagged_at: int | None) -> None:
    cols = (
        "symbol", "event_date", "run_up_5d", "run_up_10d", "run_up_class",
        "iv_atm", "implied_move_pct", "pcr", "oi_buildup_class",
        "sector_rank", "sector_trend", "growth_yoy_rev", "growth_yoy_pat",
        "fundamental_class", "expectation_proxy_score",
        "consensus_rev_est", "consensus_eps_est", "flagged_at", "created_at",
    )
    vals = [setup.get(c) for c in cols[:-2]] + [flagged_at, now]
    conn.execute(
        f"INSERT OR REPLACE INTO earnings_setups ({', '.join(cols)}) "
        f"VALUES ({', '.join(['?'] * len(cols))})",
        vals,
    )
    conn.commit()


def run_pre_screen_pass(
    conn: sqlite3.Connection,
    *,
    horizon_days: int = _DEFAULT_HORIZON_DAYS,
    now: _dt.datetime | None = None,
    sender=None,
    token: str | None = None,
    chat_id: str | None = None,
) -> dict:
    """Snapshot every result due within ``horizon_days`` and flag notable ones.

    ``sender(token, chat_id, text, thread_id)`` is injectable for tests; defaults
    to bot.dispatcher.send_telegram. A flag is sent at most once per event
    (guarded by earnings_setups.flagged_at).
    """
    import os

    from nse_data.scheduler import market_hours

    now = now or market_hours.now_ist()
    today = now.date()
    ts = int(time.time())
    if sender is None:
        from nse_data.bot.dispatcher import _topic_id, send_telegram
        sender = send_telegram
        token = token or os.environ.get("TELEGRAM_TOKEN")
        chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        thread_id = _topic_id("earnings")
    else:
        thread_id = None

    horizon = (today + _dt.timedelta(days=horizon_days)).isoformat()
    events = conn.execute(
        "SELECT symbol, expected_date FROM pending_events "
        "WHERE event_type=? AND status='upcoming' "
        "AND expected_date >= ? AND expected_date <= ?",
        (RESULT_EVENT, today.isoformat(), horizon),
    ).fetchall()

    screened = flagged = 0
    for symbol, event_date in events:
        setup = build_setup(conn, symbol, event_date, today)
        already = conn.execute(
            "SELECT flagged_at FROM earnings_setups WHERE symbol=? AND event_date=?",
            (symbol, event_date),
        ).fetchone()
        prev_flag = already[0] if already else None

        send_now = prev_flag is None and expectation.is_notable(setup)
        flagged_at = prev_flag
        if send_now:
            text = expectation.build_flag_message(symbol, event_date, setup)
            if sender(token, chat_id, text, thread_id):
                flagged_at = ts
                flagged += 1
        _upsert_setup(conn, setup, ts, flagged_at)
        screened += 1

    report = {"screened": screened, "flagged": flagged}
    log.info("pre_screen_pass", **report)
    return report


def register_pre_screen_job(scheduler, db_path: str) -> str:
    """Nightly 20:15 IST: snapshot upcoming results and flag (trading-day gated)."""
    from apscheduler.triggers.cron import CronTrigger

    from nse_data.scheduler import market_hours
    from nse_data.storage.db import open_db

    job_id = "events_pre_screen"

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        conn = open_db(db_path)
        try:
            run_pre_screen_pass(conn)
        except Exception:
            log.exception("pre_screen_pass_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=20, minute=15, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True,
    )
    return job_id
