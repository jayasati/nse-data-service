"""End-of-day summary (FEATURE_CHECKLIST Phase 8, Week 27, task 27.4).

A single Telegram message at 18:00 IST every trading day: how the market closed, sector
leaders/laggards, today's signal + paper-trade activity, and tomorrow's known events. Sends
DIRECTLY (like the morning brief), not through the gated dispatcher — every field degrades
to n/a so it always sends. Companion bookend to the 09:00 morning brief.

Registered from main.py via `register_eod_summary` (CronTrigger 18:00 IST, trading-day gated).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..market.regime_job import latest_market_state
from ..scheduler import market_hours
from ..scheduler.market_hours import IST
from ..storage.db import open_db
from .dispatcher import load_telegram_config, send_telegram

log = structlog.get_logger()
JOB_ID = "bot_eod_summary"


def _pct(p) -> str:
    return "n/a" if p is None else f"{p:+.2f}%"


def _sectors(conn: sqlite3.Connection) -> str:
    try:
        rows = conn.execute(
            "SELECT sector_name, sector_return_pct, rs_rank FROM sector_state "
            "WHERE as_of=(SELECT MAX(as_of) FROM sector_state) ORDER BY rs_rank").fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        return "Sectors: n/a"
    best, worst = rows[0], rows[-1]
    return (f"Sectors: Best {best[0]} ({_pct(best[1])}) | "
            f"Worst {worst[0]} ({_pct(worst[1])})")


def _top_movers(conn: sqlite3.Connection) -> str:
    """Day's biggest gainer + loser from raw_market_movers (re-wired W-audit orphan)."""
    try:
        rows = conn.execute(
            "SELECT symbol, direction, pct_change FROM raw_market_movers "
            "WHERE as_of=(SELECT MAX(as_of) FROM raw_market_movers) AND pct_change IS NOT NULL "
            "AND symbol NOT LIKE '%-%'"          # drop rights-entitlements / odd series (±40% noise)
        ).fetchall()
    except sqlite3.OperationalError:
        return ""
    gain = [r for r in rows if r[1] == "gainer"]
    loss = [r for r in rows if r[1] == "loser"]
    parts = []
    if gain:
        g = max(gain, key=lambda r: r[2])
        parts.append(f"↑{g[0]} {g[2]:+.1f}%")
    if loss:
        lo = min(loss, key=lambda r: r[2])
        parts.append(f"↓{lo[0]} {lo[2]:+.1f}%")
    return f"Movers: {' | '.join(parts)}" if parts else ""


def _most_active(conn: sqlite3.Connection) -> str:
    """Top names by traded volume from raw_most_active (re-wired orphan)."""
    try:
        rows = conn.execute(
            "SELECT symbol FROM raw_most_active WHERE list_type='volume' "
            "AND as_of=(SELECT MAX(as_of) FROM raw_most_active WHERE list_type='volume') "
            "ORDER BY rank LIMIT 3").fetchall()
    except sqlite3.OperationalError:
        return ""
    return f"Most active: {', '.join(r[0] for r in rows)}" if rows else ""


def _signals_today(conn: sqlite3.Connection, today: str) -> str:
    try:
        rows = conn.execute(
            "SELECT signal_type, COUNT(*) FROM signals WHERE substr(detected_at,1,10)=? "
            "GROUP BY signal_type", (today,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        return "Today's signals: none"
    parts = ", ".join(f"{t} {n}" for t, n in rows)
    return f"Today's signals: {sum(n for _, n in rows)} ({parts})"


def _smart_money(conn: sqlite3.Connection) -> str:
    """Latest smart-money composite (W22): > 0.70 accumulating, < 0.40 distributing."""
    try:
        r = conn.execute(
            "SELECT score FROM smart_money_daily ORDER BY as_of_date DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return "Smart money: n/a"
    if not r or r[0] is None:
        return "Smart money: n/a"
    s = r[0]
    lean = "accumulating" if s > 0.70 else "distributing" if s < 0.40 else "mixed"
    return f"Smart money: {s:.2f} ({lean})"


def _paper_today(conn: sqlite3.Connection, today: str) -> str:
    try:
        closed = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(net_pct),0) FROM paper_book "
            "WHERE status='closed' AND exit_date=?", (today,)).fetchone()
        opened = conn.execute(
            "SELECT COUNT(*) FROM paper_book WHERE entry_date=?", (today,)).fetchone()[0]
    except sqlite3.OperationalError:
        return "Paper: n/a"
    n, net = (closed or (0, 0.0))
    return f"Paper: {opened} opened · {n} closed (net {net:+.1f}%)"


def _tomorrow_events(conn: sqlite3.Connection, tomorrow: str) -> str:
    try:
        rows = conn.execute(
            "SELECT symbol, event_type FROM pending_events WHERE expected_date=? "
            "AND status NOT IN ('filed','expired') LIMIT 12", (tomorrow,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        return "Tomorrow: no scheduled events"
    return "Tomorrow: " + ", ".join(f"{s} ({e})" for s, e in rows)


def build_eod_summary(conn: sqlite3.Connection, now: datetime | None = None) -> str:
    now = now or market_hours.now_ist()
    today = now.date()
    tomorrow = _next_trading_day(today)
    state = latest_market_state(conn) or {}
    nifty = state.get("nifty_return_pct")
    vix = state.get("vix_level")
    vix_txt = f"{vix:.1f}" if vix is not None else "n/a"
    market = "\n".join(x for x in (_top_movers(conn), _most_active(conn)) if x)
    market_block = f"{market}\n" if market else ""
    return (
        f"📊 EOD Summary — {today.isoformat()}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"Nifty: {_pct(nifty)} | VIX: {vix_txt}\n"
        f"{_sectors(conn)}\n"
        f"{market_block}"
        f"{_smart_money(conn)}\n\n"
        f"{_signals_today(conn, today.isoformat())}\n"
        f"{_paper_today(conn, today.isoformat())}\n\n"
        f"{_tomorrow_events(conn, tomorrow.isoformat())}\n"
        "━━━━━━━━━━━━━━━━━━━"
    )


def _next_trading_day(d: date) -> date:
    nd = d + timedelta(days=1)
    for _ in range(10):
        if market_hours.is_trading_day(nd):
            return nd
        nd += timedelta(days=1)
    return nd


def send_eod_summary(db_path: str, *, sender=send_telegram) -> dict:
    token, chat_id = load_telegram_config()
    if not token or not chat_id:
        return {"skipped": "no_telegram_config"}
    conn = open_db(db_path)
    try:
        text = build_eod_summary(conn)
    finally:
        conn.close()
    return {"sent": sender(token, chat_id, text, channel="digest"), "chars": len(text)}


def register_eod_summary(scheduler: BlockingScheduler, db_path: str) -> str:
    """Attach the 18:00-IST EOD summary (task 27.4). Trading-day gated."""
    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        try:
            log.info("eod_summary", **send_eod_summary(db_path))
        except Exception:
            log.exception("eod_summary_failed")

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=18, minute=0, timezone=IST),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True)
    return JOB_ID
