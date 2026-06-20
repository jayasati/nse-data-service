"""
Telegram alert dispatcher (FEATURE_CHECKLIST Week 5, tasks 5.6–5.8).

A standalone process (its own systemd unit, task 5.9 — *not* part of the data
service) that every minute:

  1. polls `signals` for undispatched rows (dispatched = 0),
  2. re-applies the hard gates (a symbol may have been blacklisted since it
     fired),
  3. scores confidence from the live indicator context (confidence.py),
  4. if confidence > 0.65, sends the Phase-1 Telegram message and flips
     `dispatched = 1`.

It reads SQLite directly (no FastAPI yet) and `TELEGRAM_TOKEN` /
`TELEGRAM_CHAT_ID` from `.env` (task 5.7).

Backlog handling: a gated signal is marked dispatched immediately (it can never
send). A low-confidence but recent signal is *left* undispatched so it can be
re-scored next minute as its indicators evolve; once it ages past
`MAX_SIGNAL_AGE_MIN` it's marked dispatched (given up on) so the queue can't
grow without bound.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime
from datetime import time as dt_time

import structlog

from .. import universe
from ..market.regime_job import latest_market_state
from ..market.sector_map import load_sector_map
from ..market.sector_radar_job import latest_sector_ranks
from ..market.time_rules import time_rule
from ..scheduler.market_hours import is_market_open, is_trading_day, now_ist
from ..signals import enrich
from ..signals.confidence import score_confidence
from .result_alert_message import format_result_alert
from .result_quality_message import format_result_quality
from ..parsers.rating_extractor import latest_credit_by_symbol, is_junk_downgrade_kill


def _topic_id(name: str) -> int | None:
    """message_thread_id for a Telegram topic, from env (None = main channel)."""
    val = os.environ.get(f"TELEGRAM_TOPIC_{name.upper()}")
    return int(val) if val and val.lstrip("-").isdigit() else None


def _topic_for(sig: dict) -> int | None:
    """Route a signal to its Telegram topic: earnings reactions to their own
    topic, otherwise by horizon (swing vs intraday)."""
    if sig.get("signal_type") in ("earnings_direction", *_RESULT_QUALITY_TYPES,
                                  *_RESULT_ALERT_TYPES):
        return _topic_id("earnings") or _topic_id("intraday")
    return _topic_id("intraday" if sig.get("horizon") == "intraday" else "swing")
from ..signals.detect import (
    _hard_gated, _load_blacklist, _load_listing_bars, _load_price_bands,
    _load_quality_scores,
)
from ..signals.paper_tracker import compute_sl_t1
from ..storage.db import open_db

log = structlog.get_logger()

# Send only above this confidence (task 5.6).
CONFIDENCE_THRESHOLD = 0.65

# Stop re-evaluating a signal once it's this old (minutes); mark it dispatched.
MAX_SIGNAL_AGE_MIN = 15

# How many undispatched signals to consider per pass.
_POLL_LIMIT = 50

_POLL_INTERVAL_SECONDS = 60

_SIGNAL_EMOJI = "🟢"
_SIGNAL_LABELS = {
    "long_buildup": "Long Buildup",
    "breakout_52wh": "52-Week High Breakout",
    "orb_breakout": "Opening Range Breakout",
    "vwap_reclaim": "VWAP Reclaim",
    "oi_spurt": "OI Spurt",
    "earnings_direction": "Earnings Reaction",
    "result_quality_low": "Low-Quality Result",
    "result_quality_high": "Clean Result",
    "result_beat": "Result Beat",
    "result_miss": "Result Miss",
}

# Signal types that carry a fundamental verdict rather than price/volume metrics,
# so they bypass the live confidence scorer and use their own card + confidence.
_RESULT_QUALITY_TYPES = ("result_quality_low", "result_quality_high")

# Result beat/miss (18.4/18.5): own card (18.6), but scored by the live scorer
# (the card prints RSI/regime/confidence, so the live context applies).
_RESULT_ALERT_TYPES = ("result_beat", "result_miss")

# Post-event types are exempt from the 18.3 buy-rumor gate: the gate protects
# against buying INTO an exhausted pre-result run, and these only exist once
# the result is already out.
_POST_EVENT_TYPES = ("earnings_direction", *_RESULT_QUALITY_TYPES, *_RESULT_ALERT_TYPES)

# 18.3: suppress longs into a bought rumor this close to the event.
_BUY_RUMOR_MAX_DAYS = 3

# Universe gate (P2): the heading stamped on every dispatched alert, by grade.
# Membership (which grades dispatch at all) is universe.TRACKED_GRADES.
_GRADE_HEADING = {
    universe.GRADE_CORE: "CORE",
    universe.GRADE_TRADEABLE: "TRADEABLE",
    universe.GRADE_VOLATILE: "VOLATILE",
}


# ============================================================================
# Config (task 5.7)
# ============================================================================

def load_telegram_config() -> tuple[str | None, str | None]:
    """(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID) from the environment / .env."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    return os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


# ============================================================================
# Dispatch pass
# ============================================================================

def dispatch_pass(
    conn: sqlite3.Connection,
    *,
    token: str | None,
    chat_id: str | None,
    redis_client=None,
    now: datetime | None = None,
    sender=None,
) -> dict:
    """One poll-and-send sweep. Returns a report dict.

    `sender(token, chat_id, text) -> bool` is injectable for tests; defaults to
    the real Telegram HTTP call.
    """
    now = now or now_ist()
    sender = sender or send_telegram

    rows = conn.execute(
        "SELECT s.id, s.symbol, s.signal_type, s.detected_at, s.price, "
        "s.oi_change_pct, s.price_change_pct, s.volume_ratio, sf.atr_14_daily, "
        "s.fake_breakout_risk, s.horizon, COALESCE(s.direction, 'long') "
        "FROM signals s LEFT JOIN signal_features sf ON sf.signal_id = s.id "
        "WHERE s.dispatched = 0 ORDER BY s.detected_at ASC LIMIT ?",
        (_POLL_LIMIT,),
    ).fetchall()

    # Gate inputs loaded once per pass (same as the detector).
    blacklist = _load_blacklist(redis_client)
    price_bands = _load_price_bands(conn)
    listing_bars = _load_listing_bars(conn)
    market = latest_market_state(conn) or {}        # regime + divergence (7.5/9.6)
    regime = market.get("overall_regime")
    long_penalty = 0.90 if (market.get("fragile_rally") or
                            market.get("internal_weakness")) else 1.0   # task 9.6
    sector_map = load_sector_map()                  # symbol -> sector (task 8.4)
    sector_ranks = latest_sector_ranks(conn)
    quality_scores = _load_quality_scores(conn)     # fundamentals (task 14.4/14.5)
    credit_map = latest_credit_by_symbol(conn, now)  # credit ratings (Week 16)
    pre_event_map = _load_pre_event_states(conn)     # buy-rumor gate (task 18.3)
    tracked_grades = _load_tracked_grades(conn)      # universe gate (P2); {} = fail open
    rule = time_rule(now)                            # time-of-day window (task 9.1)
    threshold = rule.min_confidence or CONFIDENCE_THRESHOLD

    counts = {"sent": 0, "gated": 0, "low_confidence": 0,
              "aged_out": 0, "held": 0, "time_suppressed": 0,
              "buy_rumor_suppressed": 0, "untracked_suppressed": 0}

    for row in rows:
        sig = _row_to_signal(row)

        # Universe gate (P2): only alert on tracked (liquid) names. A symbol that
        # is graded illiquid/etf — or absent from a populated table — is dropped
        # (marked dispatched so it never requeues). Fails open: when the table is
        # missing, tracked_grades is {} and every symbol passes. The grade also
        # supplies the CORE/TRADEABLE/VOLATILE heading stamped on the alert.
        grade = tracked_grades.get(sig["symbol"].upper())
        if tracked_grades and grade not in universe.TRACKED_GRADES:
            _mark_dispatched(conn, sig["id"], now)
            counts["untracked_suppressed"] += 1
            continue
        head = _GRADE_HEADING.get(grade or "")

        series = price_bands.get(sig["symbol"], (None, None))[0]

        # Result-quality signals carry a fundamental verdict (no price/volume),
        # so they bypass the live scorer: own card, own confidence, earnings
        # topic. Gated on blacklist/T2T/listing only (NOT the fundamental
        # quality_score kill — a weak-score name can be a valid short on a bad
        # print, and a clean beat shouldn't be killed by a stale score).
        if sig.get("signal_type") in _RESULT_QUALITY_TYPES:
            if _hard_gated(sig["symbol"], series, listing_bars, blacklist, None):
                _mark_dispatched(conn, sig["id"], now)
                counts["gated"] += 1
                continue
            text, q_conf = format_result_quality(
                conn, symbol=sig["symbol"], direction=sig.get("direction", "short"),
            )
            if not text or q_conf <= CONFIDENCE_THRESHOLD:
                if _age_minutes(sig["detected_at"], now) > MAX_SIGNAL_AGE_MIN:
                    _mark_dispatched(conn, sig["id"], now)
                    counts["aged_out"] += 1
                else:
                    counts["low_confidence"] += 1
                continue
            if sender(token, chat_id, _stamp_heading(text, head), _topic_for(sig), channel="signals"):
                _mark_dispatched(conn, sig["id"], now)
                counts["sent"] += 1
            else:
                counts["held"] += 1
            continue

        # Result beat/miss (18.4–18.6): own card, live-context confidence.
        # Gated on blacklist/T2T/listing only (same reasoning as the quality
        # types: a miss is a short, so the fundamental score kill doesn't apply).
        if sig.get("signal_type") in _RESULT_ALERT_TYPES:
            if _hard_gated(sig["symbol"], series, listing_bars, blacklist, None):
                _mark_dispatched(conn, sig["id"], now)
                counts["gated"] += 1
                continue
            context = enrich.read_live_context(redis_client, sig["symbol"], conn)
            sector = sector_map.get(sig["symbol"])
            sec = sector_ranks.get(sector or "", {})
            direction = sig.get("direction", "long")
            confidence = score_confidence(
                context, None, regime,
                sector_rank=sec.get("rs_rank"), sector_trend=sec.get("rs_trend"),
                long_penalty=long_penalty, direction=direction,
                psych_state=context.get("psych_state"),
            )
            text = format_result_alert(
                conn, symbol=sig["symbol"], signal_type=sig["signal_type"],
                context=context, confidence=confidence,
            )
            if not text or confidence <= CONFIDENCE_THRESHOLD:
                if _age_minutes(sig["detected_at"], now) > MAX_SIGNAL_AGE_MIN:
                    _mark_dispatched(conn, sig["id"], now)
                    counts["aged_out"] += 1
                else:
                    counts["low_confidence"] += 1
                continue
            if sender(token, chat_id, _stamp_heading(text, head), _topic_for(sig), channel="signals"):
                _mark_dispatched(conn, sig["id"], now)
                counts["sent"] += 1
            else:
                counts["held"] += 1
            continue

        credit = credit_map.get(sig["symbol"])
        # Hard-kill longs into a recent junk downgrade (don't buy into stress).
        if _hard_gated(sig["symbol"], series, listing_bars, blacklist, quality_scores) \
                or is_junk_downgrade_kill(credit):
            _mark_dispatched(conn, sig["id"], now)
            counts["gated"] += 1
            continue

        # 18.3 hard gate: a LONG into a stock that already ran >8% with the
        # result ≤3 days away is buying an exhausted rumor — suppress it and
        # (once per symbol per day) send a BUY_RUMOR_WARNING instead.
        direction = sig.get("direction", "long")
        pre_state, pre_days, pre_run10 = pre_event_map.get(
            sig["symbol"], (None, None, None))
        if (direction == "long"
                and sig.get("signal_type") not in _POST_EVENT_TYPES
                and pre_state == "BUY_RUMOR_IN_PLAY"
                and pre_days is not None and pre_days <= _BUY_RUMOR_MAX_DAYS):
            _mark_dispatched(conn, sig["id"], now)
            counts["buy_rumor_suppressed"] += 1
            if _claim_buy_rumor_warning(conn, sig["symbol"], now):
                sender(token, chat_id,
                       _stamp_heading(build_buy_rumor_warning(sig, pre_days, pre_run10), head),
                       _topic_for(sig), channel="signals")
            continue

        # Horizon decides timing. Intraday respects the time-of-day gate
        # (09:15–09:30 NO_TRADE, lunch floor, 15:20+ NO_NEW_TRADES). Swing is
        # timing-agnostic — it can fire late-day or in the EOD batch and isn't
        # scaled by the intraday time multiplier.
        is_intraday = sig.get("horizon") == "intraday"
        if is_intraday:
            eff_suppressed, eff_multiplier = rule.suppressed, rule.multiplier
            eff_threshold = threshold
        else:
            eff_suppressed, eff_multiplier = False, 1.0
            eff_threshold = CONFIDENCE_THRESHOLD
        if eff_suppressed:
            counts["time_suppressed"] += 1
            continue

        context = enrich.read_live_context(redis_client, sig["symbol"], conn)
        sector = sector_map.get(sig["symbol"])
        sec = sector_ranks.get(sector or "", {})
        quality = quality_scores.get(sig["symbol"])
        earnings = _load_earnings_evidence(conn, sig)
        confidence = score_confidence(
            context, sig["volume_ratio"], regime,
            sector_rank=sec.get("rs_rank"), sector_trend=sec.get("rs_trend"),
            quality_score=quality,
            bb_squeeze=_load_bb_squeeze(conn, sig["symbol"]),
            bearish_divergence=_has_pattern(conn, sig["symbol"], "bearish_divergence", now),
            fake_breakout=bool(sig.get("fake_breakout_risk")),
            credit=credit,
            is_intraday=is_intraday,
            time_multiplier=eff_multiplier, long_penalty=long_penalty,
            earnings=earnings, direction=direction,
            psych_state=context.get("psych_state"),    # Layer 7 (Week 19.4)
        )

        if confidence > eff_threshold:
            text = format_message(
                sig, context, confidence, market=market,
                sector=sector, sector_info=sec, quality=quality,
                levels=_load_levels(conn, sig["symbol"]),
                delivery=_load_delivery(conn, sig["symbol"]),
                credit=credit,
            )
            if sig.get("signal_type") == "earnings_direction":
                odds_line = _earnings_odds_line(conn, direction)
                if odds_line:
                    text += "\n" + odds_line
                surprise = _implied_vs_realized_line(
                    conn, sig["symbol"], sig.get("price_change_pct"), sig.get("volume_ratio"),
                )
                if surprise:
                    text += "\n" + surprise
            if sender(token, chat_id, _stamp_heading(text, head), _topic_for(sig), channel="signals"):
                _mark_dispatched(conn, sig["id"], now)
                counts["sent"] += 1
            else:
                counts["held"] += 1   # send failed (e.g. unconfigured) — retry later
        elif _age_minutes(sig["detected_at"], now) > MAX_SIGNAL_AGE_MIN:
            _mark_dispatched(conn, sig["id"], now)
            counts["aged_out"] += 1
        else:
            counts["low_confidence"] += 1

    conn.commit()
    return counts


def _load_tracked_grades(conn: sqlite3.Connection) -> dict[str, str]:
    """{SYMBOL: grade} from tradeable_universe (the universe gate, P2).

    Returns {} when the table is absent → the gate FAILS OPEN (every symbol
    dispatches), matching universe.is_tracked's contract. Read from the dispatch
    connection so it's always the same DB the signals came from."""
    try:
        rows = conn.execute("SELECT symbol, grade FROM tradeable_universe").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {s.upper(): g for s, g in rows}


def _stamp_heading(text: str, head: str | None) -> str:
    """Prefix the grade heading (CORE/TRADEABLE/VOLATILE) onto an alert. No-op
    when head is None (fail-open / unknown grade)."""
    return f"[{head}] {text}" if head else text


def _load_pre_event_states(conn: sqlite3.Connection) -> dict:
    """{symbol: (pre_event_state, days_to_event, pre_event_run_10d)} from
    indicator_live (written nightly by events/pre_event_risk.py, refreshed
    intraday by the psychology pass). Empty when the columns aren't migrated."""
    try:
        rows = conn.execute(
            "SELECT symbol, pre_event_state, days_to_event, pre_event_run_10d "
            "FROM indicator_live WHERE pre_event_state IS NOT NULL",
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def _claim_buy_rumor_warning(conn: sqlite3.Connection, symbol: str, now: datetime) -> bool:
    """One BUY_RUMOR_WARNING per symbol per day: True only for the first claim.

    The claim is a `buy_rumor_warning` row in `signals` (dispatched=1 so the
    poll never picks it up) — giving the warning an audit trail for free."""
    today = now.date().isoformat()
    row = conn.execute(
        "SELECT 1 FROM signals WHERE symbol=? AND signal_type='buy_rumor_warning' "
        "AND detected_at LIKE ? LIMIT 1",
        (symbol, f"{today}%"),
    ).fetchone()
    if row is not None:
        return False
    conn.execute(
        "INSERT INTO signals (symbol, signal_type, detected_at, dispatched, dispatched_at) "
        "VALUES (?, 'buy_rumor_warning', ?, 1, ?)",
        (symbol, now.isoformat(), now.isoformat()),
    )
    return True


def build_buy_rumor_warning(sig: dict, days_to_event, run_10d) -> str:
    """The 18.3 message sent INSTEAD of a suppressed long signal."""
    label = _SIGNAL_LABELS.get(sig.get("signal_type", ""), sig.get("signal_type", ""))
    run_line = (f"Run-up 10d: {run_10d:+.1f}%" if isinstance(run_10d, (int, float))
                else "Run-up 10d: >8%")
    days = f"{days_to_event}d" if days_to_event is not None else "≤3d"
    return (
        f"⚠️ {sig['symbol']} — BUY RUMOR WARNING\n"
        f"Long signal suppressed ({label}).\n"
        f"{run_line} into a result due in {days} — the rumor looks bought.\n"
        f"An in-line print can still sell off. Wait for the post-result reaction."
    )


def _load_bb_squeeze(conn: sqlite3.Connection, symbol: str) -> bool:
    """True if the symbol's latest indicator_live row flags a BB squeeze."""
    try:
        row = conn.execute(
            "SELECT bb_squeeze FROM indicator_live WHERE symbol = ?", (symbol,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(row and row[0])


def _has_pattern(conn: sqlite3.Connection, symbol: str, pattern_type: str, now: datetime) -> bool:
    """True if `pattern_type` was detected for `symbol` today."""
    try:
        row = conn.execute(
            "SELECT 1 FROM patterns WHERE symbol = ? AND pattern_type = ? "
            "AND session_date = ? LIMIT 1",
            (symbol, pattern_type, now.date().isoformat()),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def _load_levels(conn: sqlite3.Connection, symbol: str) -> dict | None:
    """Latest indicator_levels row (PDH/52w/pivots) for `symbol`, or None."""
    try:
        cur = conn.execute(
            "SELECT pdh, high_52w, low_52w, r1, s1 FROM indicator_levels "
            "WHERE symbol = ? ORDER BY session_date DESC LIMIT 1",
            (symbol,),
        )
    except sqlite3.OperationalError:
        return None
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip(("pdh", "high_52w", "low_52w", "r1", "s1"), row))


def _load_delivery(conn: sqlite3.Connection, symbol: str) -> dict | None:
    """Latest delivery_conviction row for `symbol`, or None."""
    try:
        cur = conn.execute(
            "SELECT delivery_ratio, delivery_trend, delivery_conviction_score "
            "FROM delivery_conviction WHERE symbol = ? ORDER BY session_date DESC LIMIT 1",
            (symbol,),
        )
    except sqlite3.OperationalError:
        return None
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip(("delivery_ratio", "delivery_trend", "delivery_conviction_score"), row))


def _implied_vs_realized_line(
    conn: sqlite3.Connection, symbol: str, realized_move_pct, volume_ratio,
) -> str | None:
    """Implied-vs-realized surprise note for an earnings alert (S9); None on error."""
    try:
        from ..events.pre_screen import implied_vs_realized
        return implied_vs_realized(conn, symbol, realized_move_pct, volume_ratio)
    except Exception:  # noqa: BLE001 — a bonus line, never block dispatch
        return None


def _earnings_odds_line(conn: sqlite3.Connection, direction: str) -> str | None:
    """Historical-odds line for an earnings alert (None until enough samples)."""
    try:
        from ..signals.earnings_odds import compute_odds, format_odds
        return format_odds(compute_odds(conn, direction=direction))
    except Exception:  # noqa: BLE001 — odds are a bonus, never block a send
        return None


def _load_earnings_evidence(conn: sqlite3.Connection, sig: dict) -> dict | None:
    """Earnings surprise evidence for an earnings_direction signal (else None)."""
    if sig.get("signal_type") != "earnings_direction":
        return None
    try:
        from ..events.matcher import build_earnings_evidence
        ev = build_earnings_evidence(conn, sig["symbol"],
                                     reaction_direction=sig.get("direction", "long"))
    except Exception:  # noqa: BLE001 — evidence is a bonus, never block dispatch
        return None
    if ev is not None:
        ev["realized_move_pct"] = sig.get("price_change_pct")
    return ev


def _row_to_signal(row) -> dict:
    keys = ("id", "symbol", "signal_type", "detected_at", "price",
            "oi_change_pct", "price_change_pct", "volume_ratio", "atr_14_daily",
            "fake_breakout_risk", "horizon", "direction")
    return dict(zip(keys, row))


def _mark_dispatched(conn: sqlite3.Connection, signal_id: int, now: datetime) -> None:
    conn.execute(
        "UPDATE signals SET dispatched = 1, dispatched_at = ? WHERE id = ?",
        (now.isoformat(), signal_id),
    )


def _age_minutes(detected_at: str, now: datetime) -> float:
    try:
        detected = datetime.fromisoformat(detected_at)
    except ValueError:
        return 0.0
    return (now - detected).total_seconds() / 60.0


# ============================================================================
# Message formatting (task 5.8)
# ============================================================================

def format_message(
    sig: dict, context: dict, confidence: float,
    market: dict | None = None,
    sector: str | None = None, sector_info: dict | None = None,
    quality: float | None = None,
    levels: dict | None = None, delivery: dict | None = None,
    credit: dict | None = None,
) -> str:
    """Route to the intraday or swing template based on the signal's horizon."""
    if sig.get("horizon") == "intraday":
        return _format_intraday(sig, context, confidence, market, sector,
                                sector_info, levels)
    return _format_swing(sig, context, confidence, market, sector, sector_info,
                         quality, levels, delivery, credit)


def _format_intraday(sig, context, confidence, market, sector, sector_info, levels) -> str:
    """Lean, time-critical alert: VWAP/momentum + today's levels, flat by 15:15."""
    label = _SIGNAL_LABELS.get(sig["signal_type"], sig["signal_type"])
    arrow = _slope_arrow(context.get("vwap_slope"))
    sl_t1 = _format_bracket(sig.get("price"), sig.get("atr_14_daily"))
    return (
        f"⚡ {sig['symbol']} — {label} [INTRADAY]\n"
        f"OI: {_fmt(sig.get('oi_change_pct'))}% | "
        f"Price: {_fmt(sig.get('price_change_pct'))}% | "
        f"Vol: {_fmt(sig.get('volume_ratio'))}×\n\n"
        f"{_format_market(market)}"
        f"{_format_sector(sector, sector_info)}\n"
        f"VWAP {context.get('price_vs_vwap') or 'n/a'} {arrow} | "
        f"RSI(5m): {_fmt(context.get('rsi_5m'))} | "
        f"Trend: {context.get('trend_regime') or 'n/a'}\n"
        f"{_format_psychology(context)}"
        f"{_format_levels(levels)}\n"
        f"Confidence: {_tier(confidence)} ({confidence:.2f})\n"
        f"{sl_t1} | ⏰ Flat by 15:15"
    )


def _format_swing(sig, context, confidence, market, sector, sector_info,
                  quality, levels, delivery, credit) -> str:
    """Positional alert: daily trend + fundamentals/credit/delivery, hold days."""
    label = _SIGNAL_LABELS.get(sig["signal_type"], sig["signal_type"])
    sl_t1 = _format_bracket(sig.get("price"), sig.get("atr_14_daily"))
    return (
        f"📈 {sig['symbol']} — {label} [SWING]\n"
        f"Price: {_fmt(sig.get('price_change_pct'))}% | "
        f"Vol: {_fmt(sig.get('volume_ratio'))}×\n\n"
        f"{_format_market(market)}"
        f"{_format_sector(sector, sector_info)}\n"
        f"Trend: {context.get('trend_regime') or 'n/a'}\n"
        f"{_format_psychology(context)}"
        f"{_format_quality(quality)}"
        f"{_format_credit(credit)}"
        f"{_format_delivery(delivery)}"
        f"{_format_levels(levels)}\n"
        f"Confidence: {_tier(confidence)} ({confidence:.2f})\n"
        f"{sl_t1} | 📅 Hold days; trail 21-EMA"
    )


def _format_psychology(context: dict) -> str:
    """Psychological state line (task 19.5), or '' before the classifier ran."""
    state = context.get("psych_state")
    return f"Psychology: {state}\n" if state else ""


def _format_credit(credit: dict | None) -> str:
    """Credit context line for swing alerts (grade + quality + recent action)."""
    if not credit:
        return ""
    parts = []
    grade, q = credit.get("min_lt_grade"), credit.get("quality_score")
    if grade:
        parts.append(grade + (f" q{q:.0f}" if q is not None else ""))
    action, days = credit.get("action"), credit.get("days_since")
    if action and days is not None and days <= 5 and action != "reaffirm":
        parts.append(f"recent {action}")
    if credit.get("is_junk"):
        parts.append("⚠JUNK")
    return ("Credit: " + " | ".join(parts) + "\n") if parts else ""


def _format_quality(quality: float | None) -> str:
    """Quality score line (task 14.6), or '' if no fundamentals for the symbol."""
    return "" if quality is None else f"Quality: {quality:.0f}/100\n"


def _format_delivery(delivery: dict | None) -> str:
    """Delivery conviction line (task 13.6), or '' if unavailable."""
    if not delivery or delivery.get("delivery_ratio") is None:
        return ""
    ratio = delivery["delivery_ratio"]
    trend = delivery.get("delivery_trend") or "n/a"
    return f"Delivery: {trend} ({ratio * 100:.0f}%)\n"


def _format_levels(levels: dict | None) -> str:
    """Key reference levels line (task 13.6), or '' if unavailable."""
    if not levels:
        return ""
    parts = []
    if levels.get("pdh") is not None:
        parts.append(f"PDH: {levels['pdh']:.1f}")
    if levels.get("high_52w") is not None:
        parts.append(f"52wH: {levels['high_52w']:.1f}")
    return ("Levels: " + " | ".join(parts) + "\n") if parts else ""


def _tier(confidence: float) -> str:
    """Confidence tier label (task 9.3): High ≥0.80, Medium ≥0.72, else Low."""
    if confidence >= 0.80:
        return "High"
    if confidence >= 0.72:
        return "Medium"
    return "Low"


def _format_market(market: dict | None) -> str:
    """Market line: Nifty dir | VIX state ↑/↓ | Regime (+ ⚠ note). '' if absent."""
    if not market:
        return ""
    vix_arrow = {"rising": "↑", "falling": "↓"}.get(market.get("vix_direction") or "", "")
    warn = market.get("regime_warnings")
    return (
        f"Market: Nifty {market.get('nifty_direction') or 'n/a'} | "
        f"VIX {market.get('vix_state') or 'n/a'} {vix_arrow} | "
        f"Regime: {market.get('overall_regime') or 'n/a'}"
        f"{(' ' + warn) if warn else ''}\n"
    )


def _format_sector(sector: str | None, sector_info: dict | None) -> str:
    """Sector RS line, or '' if the symbol's sector is unmapped/unranked."""
    info = sector_info or {}
    rank = info.get("rs_rank")
    if not sector or rank is None:
        return ""
    label = sector.replace("NIFTY ", "")
    return f"Sector: {label} RS #{rank} | Trend: {info.get('rs_trend') or 'n/a'}\n"


def _format_bracket(price, atr) -> str:
    if price is None or atr is None or atr <= 0:
        return "SL: n/a | T1: n/a"
    sl, t1 = compute_sl_t1(price, atr)
    return f"SL: ₹{sl:.2f} | T1: ₹{t1:.2f}"


def _slope_arrow(slope) -> str:
    if slope is None:
        return ""
    return "↑" if slope > 0 else "↓"


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


# ============================================================================
# Telegram transport
# ============================================================================

def send_telegram(token: str | None, chat_id: str | None, text: str,
                  thread_id: int | None = None, *, channel: str | None = None) -> bool:
    """Deliver one message: POST to Telegram AND mirror to ntfy (notify.ntfy_send), so an
    India-blocked Telegram still reaches you. Returns True if EITHER channel delivered; no
    raise. `thread_id` routes to a Telegram topic; `channel` (signals / credit / market /
    digest) routes to a per-category ntfy topic, falling back to the catch-all NTFY_TOPIC."""
    from .notify import ntfy_send

    tg_ok = False
    if token and chat_id:
        try:
            import requests
            payload: dict = {"chat_id": chat_id, "text": text}
            if thread_id is not None:
                payload["message_thread_id"] = thread_id
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)
            tg_ok = resp.status_code == 200
            if not tg_ok:
                log.warning("telegram_send_failed", status=resp.status_code, body=resp.text[:200])
        except Exception:
            log.exception("telegram_send_error")
    else:
        log.warning("telegram_not_configured")
    ntfy_ok = ntfy_send(text, channel=channel)          # per-channel ntfy mirror (no-op if unset)
    return tg_ok or ntfy_ok


# ============================================================================
# Standalone process loop (task 5.9 — its own systemd unit)
# ============================================================================

def main(db_path: str = "data/nse.db") -> int:
    import logging
    import sys

    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    token, chat_id = load_telegram_config()
    if not token or not chat_id:
        log.warning("telegram_not_configured_at_boot",
                    hint="set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in .env")

    redis_client = _connect_redis()
    log.info("dispatcher_starting", interval_s=_POLL_INTERVAL_SECONDS)

    while True:
        try:
            # Run during market hours, AND through the EOD window so swing
            # setups confirmed near/after the close still dispatch (the EOD
            # batch). Intraday signals stay suppressed post-15:20 by the gate.
            if is_market_open() or _in_eod_window():
                conn = open_db(db_path)
                try:
                    report = dispatch_pass(
                        conn, token=token, chat_id=chat_id, redis_client=redis_client,
                    )
                finally:
                    conn.close()
                if any(report.values()):
                    log.info("dispatcher_pass", **report)
        except Exception:
            log.exception("dispatcher_pass_failed")
        time.sleep(_POLL_INTERVAL_SECONDS)


def _in_eod_window(now: datetime | None = None) -> bool:
    """Trading day, 15:20–18:30 IST — the post-close window for swing EOD sends."""
    now = now or now_ist()
    if not is_trading_day(now.date()):
        return False
    return dt_time(15, 20) <= now.time() <= dt_time(18, 30)


def _connect_redis():
    try:
        import redis  # type: ignore
        client = redis.Redis(decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
