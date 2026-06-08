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

import structlog

from ..market.regime_job import latest_market_state
from ..market.sector_map import load_sector_map
from ..market.sector_radar_job import latest_sector_ranks
from ..market.time_rules import time_rule
from ..scheduler.market_hours import is_market_open, now_ist
from ..signals import enrich
from ..signals.confidence import score_confidence
from ..parsers.rating_extractor import latest_credit_by_symbol, is_junk_downgrade_kill

# Signal types that are intraday (credit affects them only on the event day);
# everything else is treated as swing (standing credit quality applies).
INTRADAY_SIGNAL_TYPES = {"oi_spurt", "raw_oi_spurts"}
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
        "s.fake_breakout_risk "
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
    rule = time_rule(now)                            # time-of-day window (task 9.1)
    threshold = rule.min_confidence or CONFIDENCE_THRESHOLD

    counts = {"sent": 0, "gated": 0, "low_confidence": 0,
              "aged_out": 0, "held": 0, "time_suppressed": 0}

    for row in rows:
        sig = _row_to_signal(row)
        series = price_bands.get(sig["symbol"], (None, None))[0]

        credit = credit_map.get(sig["symbol"])
        # Hard-kill longs into a recent junk downgrade (don't buy into stress).
        if _hard_gated(sig["symbol"], series, listing_bars, blacklist, quality_scores) \
                or is_junk_downgrade_kill(credit):
            _mark_dispatched(conn, sig["id"], now)
            counts["gated"] += 1
            continue

        # Time-of-day suppression (09:15–09:30, 15:20+): hold, don't send. Left
        # undispatched so a NO_TRADE signal can re-evaluate once the window opens;
        # post-15:20 signals simply age out.
        if rule.suppressed:
            counts["time_suppressed"] += 1
            continue

        context = enrich.read_live_context(redis_client, sig["symbol"], conn)
        sector = sector_map.get(sig["symbol"])
        sec = sector_ranks.get(sector or "", {})
        quality = quality_scores.get(sig["symbol"])
        confidence = score_confidence(
            context, sig["volume_ratio"], regime,
            sector_rank=sec.get("rs_rank"), sector_trend=sec.get("rs_trend"),
            quality_score=quality,
            bb_squeeze=_load_bb_squeeze(conn, sig["symbol"]),
            bearish_divergence=_has_pattern(conn, sig["symbol"], "bearish_divergence", now),
            fake_breakout=bool(sig.get("fake_breakout_risk")),
            credit=credit,
            is_intraday=sig["signal_type"] in INTRADAY_SIGNAL_TYPES,
            time_multiplier=rule.multiplier, long_penalty=long_penalty,
        )

        if confidence > threshold:
            text = format_message(
                sig, context, confidence, market=market,
                sector=sector, sector_info=sec, quality=quality,
                levels=_load_levels(conn, sig["symbol"]),
                delivery=_load_delivery(conn, sig["symbol"]),
            )
            if sender(token, chat_id, text):
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


def _row_to_signal(row) -> dict:
    keys = ("id", "symbol", "signal_type", "detected_at", "price",
            "oi_change_pct", "price_change_pct", "volume_ratio", "atr_14_daily",
            "fake_breakout_risk")
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
) -> str:
    """Alert text with market + sector + stock + quality/delivery/levels context."""
    label = _SIGNAL_LABELS.get(sig["signal_type"], sig["signal_type"])
    arrow = _slope_arrow(context.get("vwap_slope"))
    sl_t1 = _format_bracket(sig.get("price"), sig.get("atr_14_daily"))

    return (
        f"{_SIGNAL_EMOJI} {sig['symbol']} — {label}\n"
        f"OI: {_fmt(sig.get('oi_change_pct'))}% | "
        f"Price: {_fmt(sig.get('price_change_pct'))}% | "
        f"Vol: {_fmt(sig.get('volume_ratio'))}×\n\n"
        f"{_format_market(market)}"
        f"{_format_sector(sector, sector_info)}\n"
        f"Stock: VWAP {context.get('price_vs_vwap') or 'n/a'} {arrow} | "
        f"RSI(5m): {_fmt(context.get('rsi_5m'))} | "
        f"Trend: {context.get('trend_regime') or 'n/a'}\n"
        f"{_format_quality(quality)}"
        f"{_format_delivery(delivery)}"
        f"{_format_levels(levels)}\n"
        f"Confidence: {_tier(confidence)} ({confidence:.2f})\n"
        f"{sl_t1} | Flat by: 15:20"
    )


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

def send_telegram(token: str | None, chat_id: str | None, text: str) -> bool:
    """POST one message to Telegram. False (no raise) if unconfigured/failed."""
    if not token or not chat_id:
        log.warning("telegram_not_configured")
        return False
    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning("telegram_send_failed", status=resp.status_code, body=resp.text[:200])
            return False
        return True
    except Exception:
        log.exception("telegram_send_error")
        return False


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
            if is_market_open():
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
