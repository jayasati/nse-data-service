"""
Morning brief (FEATURE_CHECKLIST Phase 2, Week 9, tasks 9.4/9.5).

A single Telegram message at 09:00 IST every trading day: where global markets
closed, GIFT Nifty's implied open, today's regime + posture, overnight corporate
announcements, expiry status, and Nifty's pivot S/R. Every field degrades to
"n/a" if its feed is missing, so the brief always sends.

Registered from main.py via `register_morning_brief` (CronTrigger 09:00 IST,
trading-day gated).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..indicators.levels import index_pivots
from ..market.expiry import expiry_flags
from ..market.regime_job import latest_market_state
from ..scheduler import market_hours
from ..scheduler.market_hours import IST
from ..storage.db import open_db
from .dispatcher import load_telegram_config, send_telegram

log = structlog.get_logger()

JOB_ID = "bot_morning_brief"
NIFTY = "NIFTY 50"

POSTURE = {
    "risk_on": "Lean long — favour leading-sector breakouts.",
    "neutral": "Selective — wait for clean setups, respect levels.",
    "risk_off": "Defensive — smaller size, avoid chasing strength.",
    "panic": "Stand aside — capital preservation; scalps only.",
}

_MAX_EVENTS = 8


# ============================================================================
# Field readers (all best-effort -> None)
# ============================================================================

def _macro(conn: sqlite3.Connection, asset: str) -> tuple[float | None, float | None]:
    try:
        row = conn.execute(
            "SELECT price, pct_change FROM raw_macro WHERE asset = ? "
            "ORDER BY as_of_date DESC LIMIT 1",
            (asset,),
        ).fetchone()
    except sqlite3.OperationalError:
        return (None, None)
    return (row[0], row[1]) if row else (None, None)


def _gift(conn: sqlite3.Connection) -> tuple[float | None, float | None]:
    """(pct_change, curr_value) of the latest GIFT Nifty reading."""
    try:
        row = conn.execute(
            "SELECT pct_change, curr_value FROM raw_gift_nifty "
            "ORDER BY as_of DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return (None, None)
    return (row[0], row[1]) if row else (None, None)


def _nifty_prev_close(conn: sqlite3.Connection) -> float | None:
    try:
        row = conn.execute(
            "SELECT last FROM raw_indices WHERE index_symbol = ? ORDER BY as_of DESC LIMIT 1",
            (NIFTY,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def _safe_pivots(conn: sqlite3.Connection, ref_date: date) -> dict | None:
    try:
        return index_pivots(conn, NIFTY, ref_date)
    except sqlite3.OperationalError:
        return None


def _prev_trading_day(d: date) -> date:
    dd = d - timedelta(days=1)
    for _ in range(10):
        if market_hours.is_trading_day(dd):
            return dd
        dd -= timedelta(days=1)
    return dd


def _overnight_events(conn: sqlite3.Connection, now: datetime) -> list[tuple[str, str]]:
    """(symbol, subject) announcements ingested since the last session's 15:30.

    Filtered on created_at (epoch) — overnight broadcasts are ingested when the
    collector resumes in the morning, so this window captures both the previous
    evening and the pre-open catch-up. broadcast_dt is a non-sortable
    'DD-Mon-YYYY' string, so created_at is the reliable ordering key.
    """
    cutoff = datetime.combine(_prev_trading_day(now.date()), time(15, 30), tzinfo=IST)
    try:
        rows = conn.execute(
            "SELECT symbol, subject FROM raw_announcements "
            "WHERE created_at >= ? AND (deleted_at IS NULL) "
            "ORDER BY created_at DESC LIMIT ?",
            (int(cutoff.timestamp()), _MAX_EVENTS),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(s, subj) for s, subj in rows]


# ============================================================================
# Assembly
# ============================================================================

def _dir(pct: float | None, flat: float = 0.10) -> str:
    if pct is None:
        return "n/a"
    if pct > flat:
        return "up"
    if pct < -flat:
        return "down"
    return "flat"


def _pct(p: float | None) -> str:
    return "n/a" if p is None else f"{p:+.2f}%"


def _preopen(conn: sqlite3.Connection, now: datetime) -> str:
    """Top pre-open movers from raw_pre_open (re-wired orphan), only when fresh (≤4h old) —
    at 09:00 the pre-open session (ends ~09:08) may not have landed yet, so it self-skips."""
    try:
        mx = conn.execute("SELECT MAX(as_of) FROM raw_pre_open").fetchone()[0]
    except sqlite3.OperationalError:
        return ""
    if not mx or now.timestamp() - mx > 4 * 3600:
        return ""
    rows = conn.execute(
        "SELECT symbol, pct_change FROM raw_pre_open WHERE as_of=? AND series='EQ' "
        "AND pct_change IS NOT NULL", (mx,)).fetchall()
    if not rows:
        return ""
    top = sorted(rows, key=lambda r: r[1], reverse=True)
    up = ", ".join(f"{s} {p:+.1f}%" for s, p in top[:2])
    dn = ", ".join(f"{s} {p:+.1f}%" for s, p in top[-2:][::-1])
    return f"Pre-open: ↑ {up} · ↓ {dn}\n"


def _smart_money(conn: sqlite3.Connection) -> str:
    """Smart-money composite (W22/27.5): > 0.70 accumulating, < 0.40 distributing. '' if none."""
    try:
        r = conn.execute(
            "SELECT score FROM smart_money_daily ORDER BY as_of_date DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return ""
    if not r or r[0] is None:
        return ""
    lean = "accumulating" if r[0] > 0.70 else "distributing" if r[0] < 0.40 else "mixed"
    return f"Smart money: {r[0]:.2f} ({lean})\n"


def _overnight_ratings(conn: sqlite3.Connection, now: datetime) -> str:
    """Rating actions filed since ~prior close (27.5). Parses NSE 'DD-Mon-YYYY HH:MM:SS'."""
    try:
        rows = conn.execute(
            "SELECT symbol, worst_action, min_lt_grade, agencies, broadcast_dt "
            "FROM raw_rating_actions ORDER BY id DESC LIMIT 60").fetchall()
    except sqlite3.OperationalError:
        return ""
    cutoff = now - timedelta(hours=18)            # ~15:00 prior day → covers the evening rating window
    out = []
    for sym, action, grade, agencies, bdt in rows:
        try:
            ts = datetime.strptime(bdt, "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)
        except (ValueError, TypeError):
            continue
        if ts < cutoff:
            continue
        ag = (agencies or "").split(",")[0]
        out.append(f"• {sym}: {(action or '').replace('_', ' ')} {grade or ''} ({ag})".rstrip())
    return ("Overnight rating actions:\n" + "\n".join(out[:6]) + "\n") if out else ""


def _results_today(conn: sqlite3.Connection, today: date) -> str:
    """Companies expected to report results today (27.5), from pending_events."""
    try:
        rows = conn.execute(
            "SELECT symbol FROM pending_events WHERE event_type='result' AND expected_date=? "
            "AND status NOT IN ('filed','expired') ORDER BY symbol LIMIT 15", (today.isoformat(),)
        ).fetchall()
    except sqlite3.OperationalError:
        return ""
    return f"Results today: {', '.join(r[0] for r in rows)}\n" if rows else ""


def _events_in_3d(conn: sqlite3.Connection, today: date) -> str:
    """Scheduled non-results catalysts (investor days, ex-dates, buybacks, fund-raises) in the
    next 3 days — pre-event flags for human review (no conviction direction implied)."""
    try:
        rows = conn.execute(
            "SELECT symbol, event_type, expected_date FROM pending_events "
            "WHERE status='upcoming' AND event_type != 'result' "
            "AND expected_date > ? AND expected_date <= date(?, '+3 day') "
            "ORDER BY expected_date, symbol LIMIT 15",
            (today.isoformat(), today.isoformat())).fetchall()
    except sqlite3.OperationalError:
        return ""
    if not rows:
        return ""
    items = ", ".join(f"{s} ({et} {ed[5:]})" for s, et, ed in rows)
    return f"Events ≤3d: {items}\n"


def build_brief(conn: sqlite3.Connection, now: datetime | None = None) -> str:
    now = now or market_hours.now_ist()
    today = now.date()

    gift_pct, gift_val = _gift(conn)
    prev_close = _nifty_prev_close(conn)
    expected_open = None
    if gift_val is not None:
        expected_open = gift_val
    elif prev_close is not None and gift_pct is not None:
        expected_open = prev_close * (1 + gift_pct / 100.0)

    _, sp_pct = _macro(conn, "SP500")
    _, nq_pct = _macro(conn, "NASDAQ")
    brent_price, brent_pct = _macro(conn, "BRENT")

    state = latest_market_state(conn) or {}
    regime = state.get("overall_regime")
    warnings = state.get("regime_warnings")

    flags = expiry_flags(today)
    expiry_note = _expiry_note(flags)

    pivots = _safe_pivots(conn, today)
    s1 = f"{pivots['s1']:.0f}" if pivots else "n/a"
    r1 = f"{pivots['r1']:.0f}" if pivots else "n/a"

    events = _overnight_events(conn, now)
    if events:
        ev_lines = "\n".join(f"• {sym}: {subj[:70]}" for sym, subj in events)
    else:
        ev_lines = "• none"

    open_str = f"{expected_open:,.0f}" if expected_open is not None else "n/a"
    posture = POSTURE.get(regime or "", "n/a")

    return (
        f"🌅 Market Brief — {today.isoformat()}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"GIFT Nifty: {_dir(gift_pct)} {_pct(gift_pct)} → Nifty ~{open_str}\n"
        f"{_preopen(conn, now)}"
        f"US: S&P {_pct(sp_pct)} | Nasdaq {_pct(nq_pct)}\n"
        f"Crude: ${_fmt(brent_price)} ({_pct(brent_pct)})\n\n"
        f"Today's regime: {regime or 'n/a'}"
        f"{(' ' + warnings) if warnings else ''}\n"
        f"→ {posture}\n"
        f"{_gex_line(conn)}"
        f"{_smart_money(conn)}\n"
        f"Overnight events:\n{ev_lines}\n"
        f"{_overnight_ratings(conn, now)}"
        f"{_psych_watch(conn)}\n"
        f"{_results_today(conn, today)}"
        f"{_events_in_3d(conn, today)}"
        f"Expiry: {expiry_note}\n"
        f"Nifty support: {s1} | Resistance: {r1}\n"
        "━━━━━━━━━━━━━━━━━━━"
    )


def _psych_watch(conn: sqlite3.Connection) -> str:
    """Psychology watch line (Week-19 gate): the symbols the classifier left
    tagged FOMO_EUPHORIA or CAPITULATION at the prior close. '' when none."""
    try:
        rows = conn.execute(
            "SELECT symbol, psych_state FROM indicator_live "
            "WHERE psych_state IN ('FOMO_EUPHORIA', 'CAPITULATION') "
            "ORDER BY psych_state, symbol LIMIT 12",
        ).fetchall()
    except sqlite3.OperationalError:
        return ""
    if not rows:
        return ""
    fomo = [s for s, st in rows if st == "FOMO_EUPHORIA"]
    capit = [s for s, st in rows if st == "CAPITULATION"]
    lines = ["\nPsychology watch:"]
    if fomo:
        lines.append(f"• FOMO euphoria (chase risk): {', '.join(fomo)}")
    if capit:
        lines.append(f"• Capitulation (reversal watch): {', '.join(capit)}")
    return "\n".join(lines) + "\n"


def _gex_line(conn: sqlite3.Connection) -> str:
    """Index GEX tape note (Week 23.5): positive = mean-revert, negative = trending. '' if absent."""
    try:
        r = conn.execute(
            "SELECT gex_sign, gex_flip_level FROM market_state WHERE gex_sign IS NOT NULL "
            "ORDER BY as_of DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return ""
    if not r or not r[0]:
        return ""
    tape = "mean-reverting" if r[0] == "positive" else "trending"
    flip = f" · flip ~{r[1]:.0f}" if r[1] else ""
    return f"Tape (GEX): {r[0]} → {tape}{flip}\n"


def _expiry_note(flags: dict) -> str:
    parts = []
    if flags.get("is_nifty_expiry"):
        parts.append("Nifty expiry")
    if flags.get("is_banknifty_expiry"):
        parts.append("Bank Nifty expiry")
    if flags.get("is_monthly_expiry"):
        parts.append("monthly expiry")
    return " + ".join(parts) if parts else "Not expiry day"


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:,.2f}"


# ============================================================================
# Send + scheduling
# ============================================================================

def send_morning_brief(db_path: str, *, sender=send_telegram) -> dict:
    token, chat_id = load_telegram_config()
    conn = open_db(db_path)
    try:
        text = build_brief(conn)
    finally:
        conn.close()
    sent = sender(token, chat_id, text, channel="digest")
    return {"sent": sent, "chars": len(text)}


def register_morning_brief(scheduler: BlockingScheduler, db_path: str) -> str:
    """Attach the 09:10-IST morning brief (task 9.5). Trading-day gated. 09:10 (not 09:00) so
    the pre-open session (ends ~09:08) has landed and the Pre-open line is live."""
    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            log.info("morning_brief_skipped_non_trading_day")
            return
        try:
            report = send_morning_brief(db_path)
            log.info("morning_brief", **report)
        except Exception:
            log.exception("morning_brief_failed")

    scheduler.add_job(
        _tick,
        trigger=CronTrigger(hour=9, minute=10, timezone=IST),
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return JOB_ID
