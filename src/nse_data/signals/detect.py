"""
Signal detector — the every-minute dispatcher (tasks 4.3–4.6, 4.10).

Once a minute during market hours it sweeps the tradable universe (F&O ∪ Nifty
500), applies the hard gates, evaluates the two Phase-1 rules, and writes any
fresh signal to the `signals` table — then snapshots its live indicator context
into `signal_features` for the ML archive.

Pipeline per symbol:

    hard gates (binary kills, §4.4)  →  metrics (compute.py)  →  rules (§4.5/4.6)
        →  dedup claim (dedup.py)  →  write_signal  →  enrich + feature snapshot

Hard gates are silent skips: a gated symbol produces no signal and no log line.
Some gates are global time windows (opening auction, lunch) — those short-circuit
the whole pass rather than being re-checked per symbol.

Registered from main.py via `register_signal_job`, mirroring the live-indicator
job: an APScheduler interval trigger armed at all times, gated internally on
`is_market_open()` so off-hours ticks are cheap no-ops.
"""

from __future__ import annotations

import sqlite3
import time as _time
from datetime import datetime, time

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..indicators.intraday_ohlcv import read_intraday_5m
from ..indicators.universe import live_universe
from ..scheduler.market_hours import is_market_open, now_ist
from ..storage.db import open_db
from . import compute, enrich, feature_store
from .dedup import SignalDedup

log = structlog.get_logger()

JOB_ID = "signals_detect"
_INTERVAL_SECONDS = 60

# Signal type names — the `signal_type` column values.
LONG_BUILDUP = "long_buildup"
BREAKOUT_52WH = "breakout_52wh"
ORB_BREAKOUT = "orb_breakout"        # intraday: break of the opening range high
VWAP_RECLAIM = "vwap_reclaim"        # intraday: price reclaims session VWAP
EARNINGS_DIRECTION = "earnings_direction"  # intraday: directional reaction after a result
RESULT_QUALITY_LOW = "result_quality_low"   # fundamental: low-quality beat (S3/S4)
RESULT_QUALITY_HIGH = "result_quality_high"  # fundamental: clean two-sided beat
RESULT_BEAT = "result_beat"          # 18.4: strong YoY revenue growth + positive tone
RESULT_MISS = "result_miss"          # 18.5: revenue decline or strongly negative tone

# Trade horizon per signal type — the single source of truth (the dispatcher,
# scorer, timing gate, message template and Telegram topic all key off this).
# 'intraday' = flat by 15:15; 'swing' = hold days–weeks. Default swing.
SIGNAL_HORIZON = {
    "oi_spurt": "intraday",
    "raw_oi_spurts": "intraday",
    ORB_BREAKOUT: "intraday",
    VWAP_RECLAIM: "intraday",
    EARNINGS_DIRECTION: "intraday",
    LONG_BUILDUP: "swing",
    BREAKOUT_52WH: "swing",
}

# --- earnings-reaction rule (E3) ---
# Fire on the price move 5–10 min after a result lands (with a small buffer so a
# once-a-minute sweep doesn't miss the window). Baseline = price just before the
# result; a move beyond EARNINGS_MOVE_MIN in that window declares the direction.
REACTION_MIN_MINUTES = 5
REACTION_MAX_MINUTES = 15
EARNINGS_MOVE_MIN = 1.5      # % move from the pre-result baseline to qualify
# How far back to look for a just-filed result announcement.
_REACTION_LOOKBACK_SECS = (REACTION_MAX_MINUTES + 5) * 60

# --- result beat/miss rule (18.4/18.5) ---
# Fired off the same freshly-extracted financials window as the quality rule.
RESULT_BEAT_REV_YOY_MIN = 15.0    # % — YoY revenue growth above this = beat leg
RESULT_MISS_REV_YOY_MAX = -10.0   # % — YoY revenue decline below this = miss leg

# --- result-quality rule (S4) ---
# Fundamental divergence read, fired off the freshly-extracted financials (the
# fast lane extracts within ~1 min of filing). Independent of the price move, so
# the alert can lead the repricing — SBI bled to its low ~40 min after the 2:01
# filing. Consider financials extracted in the last 30 min, but only for results
# that actually filed recently (so a nightly backfill of old PDFs can't fire it).
_QUALITY_EXTRACT_LOOKBACK_SECS = 30 * 60
_QUALITY_FILING_MAX_AGE_SECS = 2 * 86400

# Opening-range window for the ORB rule (09:15–09:30).
ORB_WINDOW_END = (9, 30)


def horizon_for(signal_type: str) -> str:
    return SIGNAL_HORIZON.get(signal_type, "swing")

# --- rule thresholds (tasks 4.5 / 4.6) ---
OI_CHANGE_MIN = 3.0       # %
PRICE_CHANGE_MIN = 1.0    # %
VOLUME_RATIO_MIN = 1.5    # ×

# --- hard-gate constants (§4.4) ---
# "Newly listed < 30 days" — approximated by trailing daily-bar count. F&O /
# Nifty-500 names all have years of history, so this only ever bites a genuine
# new listing that sneaked into the universe.
MIN_LISTING_BARS = 30
# Below this fundamental quality score, a long signal is killed (task 14.4).
QUALITY_KILL = 30
# Price band ≤ 2% → too tight to run a long into; skip for longs.
MAX_TIGHT_BAND_PCT = 2
# Trade-to-trade / restricted series — never trade these.
T2T_SERIES = frozenset({"BE", "BZ", "ST"})
# Redis set published by the pre-market loader.
_BLACKLIST_KEY = "blacklist:symbols"
# Optional broad-market regime tag, snapshotted into signal_features.
_MARKET_REGIME_KEY = "market:regime"

# Time-of-day windows where we don't fire at all.
OPENING_WINDOW_END = time(9, 30)     # skip 09:15–09:30 (opening auction noise)
LUNCH_START = time(11, 30)           # skip 11:30–13:30 (thin, chop-prone)
LUNCH_END = time(13, 30)


# ============================================================================
# Pass orchestration (task 4.3)
# ============================================================================

def run_detection_pass(
    conn: sqlite3.Connection,
    *,
    redis_client=None,
    dedup: SignalDedup | None = None,
    now: datetime | None = None,
) -> dict:
    """One detection sweep. Returns a small report dict.

    `redis_client` powers the blacklist read, the dedup guard, and the live
    context enrichment — all degrade gracefully when it is None. `dedup` is
    injectable for tests; otherwise one is built around `redis_client`.
    """
    now = now or now_ist()
    if not is_market_open(now):
        return {"skipped": "market_closed"}

    dedup = dedup or SignalDedup(redis_client)
    detected_at = now.isoformat()
    market_regime = _market_regime(redis_client)

    # Earnings reactions are event-driven, so they're evaluated every minute
    # (including the opening/lunch windows the Phase-1 rules skip) — but only for
    # the few symbols whose result just filed.
    earnings_fired = _detect_earnings_reactions(
        conn, redis_client, dedup, detected_at, market_regime, now,
    )
    # Fundamental quality read off the freshly-extracted financials — also
    # event-driven, so evaluated every minute (incl. opening/lunch windows).
    quality_fired = _detect_result_quality(
        conn, redis_client, dedup, detected_at, market_regime, now,
    )
    # Result beat/miss (18.4/18.5) — same fresh-extraction window, but keyed to
    # the headline revenue print + press-release tone rather than the
    # operating-line divergence the quality rule reads.
    result_fired = _detect_result_beat_miss(
        conn, redis_client, dedup, detected_at, market_regime, now,
    )

    window = _time_window_skip(now)
    if window is not None:
        return {
            "skipped": window, "earnings": earnings_fired, "quality": quality_fired,
            "result_beat_miss": result_fired,
        }

    symbols = live_universe(conn)
    blacklist = _load_blacklist(redis_client)
    price_bands = _load_price_bands(conn)
    listing_bars = _load_listing_bars(conn)
    quality_scores = _load_quality_scores(conn)          # task 14.4
    fresh_52w_highs = _load_today_52w_highs(conn, now)

    fired = {LONG_BUILDUP: 0, BREAKOUT_52WH: 0, ORB_BREAKOUT: 0, VWAP_RECLAIM: 0}
    gated = 0

    for symbol in symbols:
        series, band = price_bands.get(symbol, (None, None))
        if _hard_gated(symbol, series, listing_bars, blacklist, quality_scores):
            gated += 1
            continue

        # Both Phase-1 signals are longs, so the tight-band gate applies to both.
        band_too_tight = band is not None and band <= MAX_TIGHT_BAND_PCT

        for signal_type in (LONG_BUILDUP, BREAKOUT_52WH, ORB_BREAKOUT, VWAP_RECLAIM):
            if band_too_tight:
                continue
            metrics = _evaluate(conn, symbol, signal_type, fresh_52w_highs, now)
            if metrics is None:
                continue
            if not dedup.claim(symbol, signal_type):
                continue   # already fired this setup within the TTL
            _emit(
                conn, redis_client,
                symbol=symbol, signal_type=signal_type,
                detected_at=detected_at, metrics=metrics,
                market_regime=market_regime,
            )
            fired[signal_type] += 1

    return {
        "symbols": len(symbols),
        "gated": gated,
        "fired": fired,
        "earnings": earnings_fired,
        "quality": quality_fired,
        "result_beat_miss": result_fired,
        "signals": sum(fired.values()) + earnings_fired + quality_fired + result_fired,
    }


# ============================================================================
# Earnings-reaction detection (E3)
# ============================================================================

def _detect_earnings_reactions(
    conn: sqlite3.Connection,
    redis_client,
    dedup: SignalDedup,
    detected_at: str,
    market_regime: str | None,
    now: datetime,
) -> int:
    """Fire earnings_direction for symbols whose result just filed and is moving.

    One DB read finds the handful of symbols with a result announcement in the
    last ~20 min; each is checked for a directional move 5–10 min after the
    filing. Hard gates (blacklist/T2T/listing) still apply; the quality kill does
    NOT (a low-quality name can be a valid short on a bad result)."""
    contexts = _load_reaction_contexts(conn, now)
    if not contexts:
        return 0
    blacklist = _load_blacklist(redis_client)
    listing_bars = _load_listing_bars(conn)
    fired = 0
    for symbol, reaction in contexts.items():
        if symbol in blacklist:
            continue
        if listing_bars.get(symbol, 0) < MIN_LISTING_BARS:
            continue
        metrics = _rule_earnings_direction(conn, symbol, now, reaction)
        if metrics is None:
            continue
        if not dedup.claim(symbol, EARNINGS_DIRECTION):
            continue
        _emit(
            conn, redis_client,
            symbol=symbol, signal_type=EARNINGS_DIRECTION,
            detected_at=detected_at, metrics=metrics, market_regime=market_regime,
        )
        fired += 1
    return fired


def _load_reaction_contexts(
    conn: sqlite3.Connection, now: datetime,
) -> dict[str, dict]:
    """{symbol: {result_ts, baseline}} for results filed in the last ~20 min.

    Source: result-subject announcements whose broadcast_dt is recent. The
    baseline is the price just before the filing (last 5-min bar at/under
    result_ts), so the reaction move is measured from the pre-result level.
    """
    from ..fundamentals.from_results import is_result_subject

    if not _has_table(conn, "raw_announcements"):
        return {}
    cutoff = now.timestamp() - _REACTION_LOOKBACK_SECS
    rows = conn.execute(
        "SELECT symbol, subject, broadcast_dt FROM raw_announcements "
        "WHERE broadcast_dt IS NOT NULL ORDER BY broadcast_dt DESC LIMIT 200"
    ).fetchall()
    out: dict[str, dict] = {}
    for symbol, subject, broadcast_dt in rows:
        if symbol in out or not is_result_subject(subject):
            continue
        result_ts = _broadcast_epoch(broadcast_dt, now)
        if result_ts is None or result_ts < cutoff or result_ts > now.timestamp():
            continue
        baseline = _reaction_baseline(conn, symbol, result_ts)
        if baseline is None:
            continue
        out[symbol] = {"result_ts": result_ts, "baseline": baseline}
    return out


def _broadcast_epoch(broadcast_dt: str, now: datetime) -> float | None:
    """Parse an NSE broadcast timestamp ('30-Apr-2026 17:19:41') to epoch (IST)."""
    from datetime import datetime as _dtc

    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = _dtc.strptime(broadcast_dt.strip(), fmt)
            return dt.replace(tzinfo=now.tzinfo).timestamp()
        except (ValueError, AttributeError):
            continue
    return None


def _reaction_baseline(
    conn: sqlite3.Connection, symbol: str, result_ts: float,
) -> float | None:
    """Price just before the result: last 5-min bar close at/under result_ts."""
    bars = read_intraday_5m(conn, symbol, since_ts=int(result_ts) - 3600)
    if bars is None or bars.empty:
        return None
    pre = bars[bars.index <= int(result_ts)]
    if not pre.empty:
        return float(pre["close"].iloc[-1])
    # No pre-result bar in the last hour (result near open): use the first bar.
    return float(bars["close"].iloc[0])


def _rule_earnings_direction(
    conn: sqlite3.Connection, symbol: str, now: datetime, reaction: dict,
) -> dict | None:
    """Directional move 5–10 min after a result vs the pre-result baseline.

    direction = sign of the move; magnitude must clear EARNINGS_MOVE_MIN. Returns
    a metrics dict (with ``direction`` + ``realized_move_pct``) or None.
    """
    elapsed_min = (now.timestamp() - reaction["result_ts"]) / 60.0
    if not (REACTION_MIN_MINUTES <= elapsed_min <= REACTION_MAX_MINUTES):
        return None
    baseline = reaction["baseline"]
    _, price = compute.compute_price_change(conn, symbol)
    if price is None or baseline in (None, 0):
        return None
    move_pct = (price - baseline) / baseline * 100.0
    if abs(move_pct) < EARNINGS_MOVE_MIN:
        return None
    volume_ratio = compute.compute_volume_ratio(conn, symbol, now=now)
    return {
        "price": price,
        "oi_change_pct": None,
        "price_change_pct": round(move_pct, 2),
        "realized_move_pct": round(move_pct, 2),
        "volume_ratio": volume_ratio,
        "direction": "long" if move_pct > 0 else "short",
    }


# ============================================================================
# Result-quality detection (S4) — fundamental divergence, not price
# ============================================================================

def _detect_result_quality(
    conn: sqlite3.Connection,
    redis_client,
    dedup: SignalDedup,
    detected_at: str,
    market_regime: str | None,
    now: datetime,
) -> int:
    """Fire result_quality_low/high off financials extracted in the last ~30 min.

    Reads the PDF-derived growth (``growth_json``) + the treasury level field and
    classifies earnings quality (no price, no history). A low-quality beat
    (headline PAT up while the operating line falls) leans short; a clean
    two-sided beat leans long. Hard gates (blacklist/listing) apply; the
    fundamental quality_score kill does NOT (a weak-quality name can be a valid
    short on a bad print, and a strong print shouldn't be killed by a stale score)."""
    if not _has_table(conn, "extracted_financials"):
        return 0
    from ..fundamentals.from_results import quarter_growth
    from ..fundamentals.sectors import classify_result

    extract_cutoff = int(now.timestamp()) - _QUALITY_EXTRACT_LOOKBACK_SECS
    rows = conn.execute(
        "SELECT symbol, period_ending, growth_json, broadcast_dt, "
        "profit_on_sale_of_investments_cr, narrative_json "
        "FROM extracted_financials "
        "WHERE scope = 'standalone' AND extracted_at >= ? "
        "ORDER BY extracted_at DESC LIMIT 50",
        (extract_cutoff,),
    ).fetchall()
    if not rows:
        return 0

    import json as _json
    blacklist = _load_blacklist(redis_client)
    listing_bars = _load_listing_bars(conn)
    fired = 0
    seen: set[str] = set()
    for symbol, period_ending, growth_json, broadcast_dt, treasury, narrative_json in rows:
        if symbol in seen:           # newest row per symbol only
            continue
        seen.add(symbol)
        if symbol in blacklist or listing_bars.get(symbol, 0) < MIN_LISTING_BARS:
            continue
        # Only fire for a result that actually filed recently — guards against a
        # nightly backfill of old PDFs (which stamps a fresh extracted_at).
        filed_ts = _broadcast_epoch(broadcast_dt, now) if broadcast_dt else None
        if filed_ts is None or (now.timestamp() - filed_ts) > _QUALITY_FILING_MAX_AGE_SECS:
            continue
        # Growth comes from the PDF's OWN comparative columns (the filing prints
        # current / preceding-quarter / year-ago) — text-anchored at extraction so
        # it's reliable even where the vision model's column read isn't. Stored
        # history is only a last resort when the filing carried no comparatives.
        growth = {}
        if growth_json:
            try:
                growth = _json.loads(growth_json)
            except (ValueError, TypeError):
                growth = {}
        if not growth:
            growth = quarter_growth(conn, symbol, period_ending, "standalone")
        if not growth:
            continue
        # Route through the sector registry: BFSI uses its validated rule; any
        # non-BFSI (out-of-scope) sector is guarded to a neutral verdict, so the
        # detector never fires a result_quality signal on a sector it can't yet
        # read (the P1 guard — fixes the ONGC false LONG). See sectors/README.md.
        # The press-release narrative (guidance/volumes/FDA — P7, lifted at
        # extraction) is folded into the verdict when the filing carried one.
        narrative = None
        if narrative_json:
            try:
                narrative = _json.loads(narrative_json)
            except (ValueError, TypeError):
                narrative = None
        verdict = classify_result(
            symbol, growth, {"profit_on_sale_of_investments_cr": treasury},
            narrative=narrative,
        )
        if verdict.label == "neutral":
            continue
        signal_type = RESULT_QUALITY_LOW if verdict.label == "low" else RESULT_QUALITY_HIGH
        if not dedup.claim(symbol, signal_type):
            continue
        _, price = compute.compute_price_change(conn, symbol)
        _emit(
            conn, redis_client,
            symbol=symbol, signal_type=signal_type, detected_at=detected_at,
            metrics={
                "price": price,
                "oi_change_pct": None,
                "price_change_pct": None,
                "volume_ratio": None,
                "direction": verdict.direction or "long",
                "quality_flags": verdict.flags,
                "quality_summary": verdict.summary,
            },
            market_regime=market_regime,
        )
        log.info(
            "result_quality_signal", symbol=symbol, label=verdict.label,
            direction=verdict.direction, flags=verdict.flags, summary=verdict.summary,
        )
        fired += 1
    return fired


# ============================================================================
# Result beat/miss detection (18.4 / 18.5)
# ============================================================================

def result_beat_check(growth: dict | None, narrative: dict | None) -> bool:
    """18.4: YoY revenue growth > +15% AND the filing's sentiment is positive.

    "Sentiment positive" = the press-release narrative's management tone is
    positive, or guidance was raised (the strongest positive statement a filing
    can make). A filing with no narrative carries no sentiment → no beat signal
    (the rule requires BOTH legs).
    """
    if not growth or not narrative:
        return False
    rev = growth.get("yoy_revenue_pct")
    if not isinstance(rev, (int, float)) or rev <= RESULT_BEAT_REV_YOY_MIN:
        return False
    return narrative.get("mgmt_tone") == "positive" or narrative.get("guidance") == "raised"


def result_miss_check(growth: dict | None, narrative: dict | None) -> bool:
    """18.5: YoY revenue growth < −10% OR strongly negative sentiment.

    "Strongly negative" = negative management tone corroborated by a guidance
    cut or a falling headline PAT — tone alone is too weak to short on.
    """
    growth = growth or {}
    narrative = narrative or {}
    rev = growth.get("yoy_revenue_pct")
    if isinstance(rev, (int, float)) and rev < RESULT_MISS_REV_YOY_MAX:
        return True
    if narrative.get("mgmt_tone") != "negative":
        return False
    pat = growth.get("yoy_pat_pct")
    return narrative.get("guidance") == "cut" or (
        isinstance(pat, (int, float)) and pat < 0
    )


def _detect_result_beat_miss(
    conn: sqlite3.Connection,
    redis_client,
    dedup: SignalDedup,
    detected_at: str,
    market_regime: str | None,
    now: datetime,
) -> int:
    """Fire result_beat / result_miss off financials extracted in the last ~30 min.

    Same freshness + recent-filing guards as the quality rule (so a nightly
    backfill of old PDFs can't fire it). The earnings-quality verdict is carried
    on the metrics as a HIGH/LOW flag for the alert card (18.4 "include
    earnings quality flag"). Hard gates: blacklist + listing only — the
    fundamental quality_score kill doesn't apply (a miss is a short)."""
    if not _has_table(conn, "extracted_financials"):
        return 0
    import json as _json

    from ..fundamentals.from_results import quarter_growth
    from ..fundamentals.sectors import classify_result

    extract_cutoff = int(now.timestamp()) - _QUALITY_EXTRACT_LOOKBACK_SECS
    rows = conn.execute(
        "SELECT symbol, period_ending, growth_json, broadcast_dt, "
        "profit_on_sale_of_investments_cr, narrative_json "
        "FROM extracted_financials "
        "WHERE scope = 'standalone' AND extracted_at >= ? "
        "ORDER BY extracted_at DESC LIMIT 50",
        (extract_cutoff,),
    ).fetchall()
    if not rows:
        return 0

    blacklist = _load_blacklist(redis_client)
    listing_bars = _load_listing_bars(conn)
    fired = 0
    seen: set[str] = set()
    for symbol, period_ending, growth_json, broadcast_dt, treasury, narrative_json in rows:
        if symbol in seen:
            continue
        seen.add(symbol)
        if symbol in blacklist or listing_bars.get(symbol, 0) < MIN_LISTING_BARS:
            continue
        filed_ts = _broadcast_epoch(broadcast_dt, now) if broadcast_dt else None
        if filed_ts is None or (now.timestamp() - filed_ts) > _QUALITY_FILING_MAX_AGE_SECS:
            continue
        growth = {}
        if growth_json:
            try:
                growth = _json.loads(growth_json)
            except (ValueError, TypeError):
                growth = {}
        if not growth:
            growth = quarter_growth(conn, symbol, period_ending, "standalone")
        narrative = None
        if narrative_json:
            try:
                narrative = _json.loads(narrative_json)
            except (ValueError, TypeError):
                narrative = None

        if result_beat_check(growth, narrative):
            signal_type, direction = RESULT_BEAT, "long"
        elif result_miss_check(growth, narrative):
            signal_type, direction = RESULT_MISS, "short"
        else:
            continue
        if not dedup.claim(symbol, signal_type):
            continue
        # Earnings-quality flag for the alert card: the sector-routed verdict
        # (BFSI rule / GENERIC guard). A 'low' verdict marks the beat LOW.
        verdict = classify_result(
            symbol, growth, {"profit_on_sale_of_investments_cr": treasury},
            narrative=narrative,
        )
        _, price = compute.compute_price_change(conn, symbol)
        _emit(
            conn, redis_client,
            symbol=symbol, signal_type=signal_type, detected_at=detected_at,
            metrics={
                "price": price,
                "oi_change_pct": None,
                "price_change_pct": None,
                "volume_ratio": None,
                "direction": direction,
                "quality_flag": "LOW" if verdict.label == "low" else "HIGH",
            },
            market_regime=market_regime,
        )
        log.info(
            "result_beat_miss_signal", symbol=symbol, signal_type=signal_type,
            yoy_revenue=growth.get("yoy_revenue_pct"),
            tone=(narrative or {}).get("mgmt_tone"),
            quality="LOW" if verdict.label == "low" else "HIGH",
        )
        fired += 1
    return fired


def _evaluate(
    conn: sqlite3.Connection,
    symbol: str,
    signal_type: str,
    fresh_52w_highs: set[str],
    now: datetime,
) -> dict | None:
    """Return the signal's metric dict if its rule fires, else None.

    Metrics are computed lazily and cheapest-first so the per-minute sweep
    doesn't pay for an intraday read on symbols that fail an early leg.
    """
    if signal_type == LONG_BUILDUP:
        return _rule_long_buildup(conn, symbol, now)
    if signal_type == BREAKOUT_52WH:
        return _rule_breakout_52wh(conn, symbol, fresh_52w_highs, now)
    if signal_type == ORB_BREAKOUT:
        return _rule_orb_breakout(conn, symbol, now)
    if signal_type == VWAP_RECLAIM:
        return _rule_vwap_reclaim(conn, symbol, now)
    return None


# ============================================================================
# Rules (tasks 4.5 / 4.6)
# ============================================================================

def _rule_long_buildup(
    conn: sqlite3.Connection, symbol: str, now: datetime,
) -> dict | None:
    """oi_change_pct ≥ +3.0  AND  price_change_pct ≥ +1.0  AND  vol_ratio ≥ 1.5."""
    oi_change = compute.compute_oi_change(conn, symbol)[0]
    if oi_change is None or oi_change < OI_CHANGE_MIN:
        return None

    price_change, price = compute.compute_price_change(conn, symbol)
    if price_change is None or price_change < PRICE_CHANGE_MIN:
        return None

    volume_ratio = compute.compute_volume_ratio(conn, symbol, now=now)
    if volume_ratio is None or volume_ratio < VOLUME_RATIO_MIN:
        return None

    return {
        "price": price,
        "oi_change_pct": oi_change,
        "price_change_pct": price_change,
        "volume_ratio": volume_ratio,
    }


def _rule_breakout_52wh(
    conn: sqlite3.Connection,
    symbol: str,
    fresh_52w_highs: set[str],
    now: datetime,
) -> dict | None:
    """New 52-week high today  AND  vol_ratio ≥ 1.5."""
    if symbol not in fresh_52w_highs:
        return None

    volume_ratio = compute.compute_volume_ratio(conn, symbol, now=now)
    if volume_ratio is None or volume_ratio < VOLUME_RATIO_MIN:
        return None

    # Price/OI context is informational for this rule; capture what's available.
    price_change, price = compute.compute_price_change(conn, symbol)
    oi_change = compute.compute_oi_change(conn, symbol)[0]
    return {
        "price": price,
        "oi_change_pct": oi_change,
        "price_change_pct": price_change,
        "volume_ratio": volume_ratio,
        "fake_breakout_risk": _fake_breakout_risk(conn, symbol, price, volume_ratio, now),
    }


def _fake_breakout_risk(
    conn: sqlite3.Connection, symbol: str, price: float | None,
    volume_ratio: float | None, now: datetime,
) -> int:
    """Task 15.3: 1 when the breakout shows wick rejection on weak volume.

    Wick rejection = the live price sits in the *lower half* of today's session
    range (the high was sold into). Weak volume = volume_ratio < 1.2×. Both must
    hold for the risk flag.
    """
    if price is None or (volume_ratio is not None and volume_ratio >= 1.2):
        return 0
    session_open = int(now.replace(hour=9, minute=15, second=0, microsecond=0).timestamp())
    bars = read_intraday_5m(conn, symbol, since_ts=session_open)
    if bars.empty:
        return 0
    hi, lo = float(bars["high"].max()), float(bars["low"].min())
    if hi <= lo:
        return 0
    close_position = (price - lo) / (hi - lo)     # 0 = at low, 1 = at high
    return 1 if close_position < 0.5 else 0


# ============================================================================
# Intraday rules (ORB + VWAP reclaim) — horizon='intraday'
# ============================================================================

def _session_bars(conn, symbol, now):
    """Today's 5-minute bars from the 09:15 open (ts-indexed, ascending)."""
    session_open = int(now.replace(hour=9, minute=15, second=0, microsecond=0).timestamp())
    return read_intraday_5m(conn, symbol, since_ts=session_open)


def _session_vwap(bars):
    """Cumulative session VWAP series aligned to `bars` (typical-price weighted)."""
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    cum_vol = bars["volume"].cumsum()
    return (typical * bars["volume"]).cumsum() / cum_vol.replace(0, float("nan"))


def _rule_orb_breakout(
    conn: sqlite3.Connection, symbol: str, now: datetime,
) -> dict | None:
    """Long when price breaks above the opening-range (09:15–09:30) high, with
    volume confirmation. Intraday — exits same day."""
    or_end = now.replace(hour=ORB_WINDOW_END[0], minute=ORB_WINDOW_END[1],
                         second=0, microsecond=0)
    if now < or_end:                      # opening range not complete yet
        return None
    bars = _session_bars(conn, symbol, now)
    if bars.empty:
        return None
    or_end_ts = int(or_end.timestamp())
    or_bars = bars[bars.index < or_end_ts]
    post_bars = bars[bars.index >= or_end_ts]
    if or_bars.empty or post_bars.empty:
        return None

    orh = float(or_bars["high"].max())
    last_close = float(post_bars["close"].iloc[-1])
    if last_close <= orh:                 # no breakout above the range high
        return None

    volume_ratio = compute.compute_volume_ratio(conn, symbol, now=now)
    if volume_ratio is None or volume_ratio < VOLUME_RATIO_MIN:
        return None
    price_change, _ = compute.compute_price_change(conn, symbol)
    return {
        "price": last_close,
        "oi_change_pct": None,
        "price_change_pct": price_change,
        "volume_ratio": volume_ratio,
    }


def _rule_vwap_reclaim(
    conn: sqlite3.Connection, symbol: str, now: datetime,
) -> dict | None:
    """Long when price was below session VWAP earlier and reclaims it (crosses
    back above on the latest bar), with volume confirmation. Intraday."""
    bars = _session_bars(conn, symbol, now)
    if len(bars) < 3:                     # need some session history
        return None
    vwap = _session_vwap(bars)
    close = bars["close"]
    if vwap.isna().iloc[-1] or vwap.isna().iloc[-2]:
        return None

    latest_above = close.iloc[-1] > vwap.iloc[-1]
    prev_below = close.iloc[-2] <= vwap.iloc[-2]
    spent_time_below = bool((close.iloc[:-1] < vwap.iloc[:-1]).any())
    if not (latest_above and prev_below and spent_time_below):
        return None

    volume_ratio = compute.compute_volume_ratio(conn, symbol, now=now)
    if volume_ratio is None or volume_ratio < VOLUME_RATIO_MIN:
        return None
    price_change, _ = compute.compute_price_change(conn, symbol)
    return {
        "price": float(close.iloc[-1]),
        "oi_change_pct": None,
        "price_change_pct": price_change,
        "volume_ratio": volume_ratio,
    }


# ============================================================================
# Hard gates (task 4.4)
# ============================================================================

def _hard_gated(
    symbol: str,
    series: str | None,
    listing_bars: dict[str, int],
    blacklist: set[str],
    quality_scores: dict[str, float] | None = None,
) -> bool:
    """True if any binary-kill gate trips (symbol-level, time-independent).

    Time-window gates (opening auction, lunch) are handled once per pass in
    `_time_window_skip`, not here. The promoter-pledge gate (§4.4) has no data
    source yet (pledge isn't collected — only promoter holding %), so it is a
    deliberate no-op.

    Quality gate (task 14.4): kill a long signal whose fundamental quality score
    is below 30. A None score (no fundamentals available) does NOT kill — absence
    of data isn't evidence of low quality. All Phase-1 signals are longs.
    """
    if symbol in blacklist:
        return True
    if series in T2T_SERIES:                              # T2T / restricted series
        return True
    if listing_bars.get(symbol, 0) < MIN_LISTING_BARS:   # newly listed < ~30 days
        return True
    if quality_scores is not None:
        q = quality_scores.get(symbol)
        if q is not None and q < QUALITY_KILL:
            return True
    return False


def _load_quality_scores(conn: sqlite3.Connection) -> dict[str, float]:
    """{symbol: quality_score} from stock_fundamentals (empty if table absent)."""
    try:
        rows = conn.execute(
            "SELECT symbol, quality_score FROM stock_fundamentals "
            "WHERE quality_score IS NOT NULL"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {s: q for s, q in rows}


def _time_window_skip(now: datetime) -> str | None:
    """Return a skip-reason string if `now` is inside a no-fire window, else None."""
    t = now.timetz().replace(tzinfo=None)
    if t <= OPENING_WINDOW_END:           # 09:15–09:30 opening auction
        return "opening_window"
    if LUNCH_START <= t <= LUNCH_END:     # 11:30–13:30 lunch chop
        return "lunch_window"
    return None


# ============================================================================
# Persistence + enrichment
# ============================================================================

def write_signal(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    signal_type: str,
    detected_at: str,
    price: float | None,
    oi_change_pct: float | None,
    price_change_pct: float | None,
    volume_ratio: float | None,
    confidence: float | None = None,
    fake_breakout_risk: int = 0,
    horizon: str | None = None,
    direction: str = "long",
) -> int:
    """Insert one row into `signals`. Returns the new signal id.

    `confidence` is left NULL here — the Week-5 scorer fills it on dispatch.
    `fake_breakout_risk` (task 15.3) trims confidence on dispatch when set.
    `horizon` (swing/intraday) defaults from the signal type when not given.
    `direction` ('long'/'short') is 'long' for the Phase-1 rules; earnings
    reactions can be short.
    """
    horizon = horizon or horizon_for(signal_type)
    cur = conn.execute(
        "INSERT INTO signals "
        "(symbol, signal_type, detected_at, price, oi_change_pct, "
        " price_change_pct, volume_ratio, confidence, fake_breakout_risk, horizon, direction) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (symbol, signal_type, detected_at, price, oi_change_pct,
         price_change_pct, volume_ratio, confidence, fake_breakout_risk, horizon, direction),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def _emit(
    conn: sqlite3.Connection,
    redis_client,
    *,
    symbol: str,
    signal_type: str,
    detected_at: str,
    metrics: dict,
    market_regime: str | None,
) -> int:
    """Write the signal row + snapshot its live features. Returns the signal id."""
    signal_id = write_signal(
        conn,
        symbol=symbol, signal_type=signal_type, detected_at=detected_at,
        price=metrics.get("price"),
        oi_change_pct=metrics.get("oi_change_pct"),
        price_change_pct=metrics.get("price_change_pct"),
        volume_ratio=metrics.get("volume_ratio"),
        fake_breakout_risk=metrics.get("fake_breakout_risk", 0),
        direction=metrics.get("direction", "long"),
    )
    context = enrich.read_live_context(redis_client, symbol, conn)
    feature_store.snapshot_features(
        conn, signal_id, symbol, detected_at, context,
        volume_ratio=metrics.get("volume_ratio"),
        market_regime=market_regime,
    )
    log.info(
        "signal_fired", symbol=symbol, signal_type=signal_type,
        signal_id=signal_id, **{k: metrics.get(k) for k in
                                ("oi_change_pct", "price_change_pct", "volume_ratio")},
    )
    return signal_id


# ============================================================================
# Batch gate-data loads (one query each per pass)
# ============================================================================

def _load_blacklist(redis_client) -> set[str]:
    """Surveillance blacklist published by the pre-market loader; empty if down."""
    if redis_client is None:
        return set()
    try:
        return set(redis_client.smembers(_BLACKLIST_KEY))
    except Exception:
        return set()


def _market_regime(redis_client) -> str | None:
    """Broad-market regime tag, or None if no publisher / Redis down."""
    if redis_client is None:
        return None
    try:
        value = redis_client.get(_MARKET_REGIME_KEY)
        return value or None
    except Exception:
        return None


def _load_price_bands(conn: sqlite3.Connection) -> dict[str, tuple[str | None, int | None]]:
    """{symbol: (series, band)} from raw_price_bands. Empty if table absent."""
    if not _has_table(conn, "raw_price_bands"):
        return {}
    return {
        r[0]: (r[1], r[2])
        for r in conn.execute("SELECT symbol, series, band FROM raw_price_bands")
    }


def _load_listing_bars(conn: sqlite3.Connection) -> dict[str, int]:
    """{symbol: trailing EQ daily-bar count} — the newly-listed proxy."""
    return {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT symbol, COUNT(*) FROM raw_bhavcopy_cm "
            "WHERE series = 'EQ' GROUP BY symbol"
        )
    }


def _load_today_52w_highs(conn: sqlite3.Connection, now: datetime) -> set[str]:
    """Symbols that printed a fresh 52-week high in today's session.

    `raw_high_low_52w.as_of` is epoch seconds; we scope to ≥ today's 09:15 IST
    so a high from a prior session doesn't keep re-firing the breakout rule.
    """
    if not _has_table(conn, "raw_high_low_52w"):
        return set()
    session_start = int(
        now.replace(hour=9, minute=15, second=0, microsecond=0).timestamp()
    )
    return {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT symbol FROM raw_high_low_52w "
            "WHERE event = 'high' AND as_of >= ?",
            (session_start,),
        )
    }


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None


# ============================================================================
# Scheduling (task 4.10)
# ============================================================================

def run_detection_job(db_path: str) -> dict:
    """Open a per-invocation connection, run one pass, return the report."""
    if not is_market_open():
        return {"skipped": "market_closed"}

    started = _time.time()
    redis_client = _connect_redis()
    conn = open_db(db_path)
    try:
        report = run_detection_pass(conn, redis_client=redis_client)
    finally:
        conn.close()
    report["elapsed_secs"] = round(_time.time() - started, 2)
    return report


def register_signal_job(scheduler: BlockingScheduler, db_path: str) -> str:
    """Attach the every-minute signal detector to the running scheduler."""
    def _tick():
        try:
            report = run_detection_job(db_path)
            if "skipped" not in report:
                log.info("signals_detect_tick", **report)
        except Exception:
            log.exception("signals_detect_failed")

    scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(seconds=_INTERVAL_SECONDS),
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return JOB_ID


def _connect_redis():
    """Best-effort Redis client; None if unavailable (gates/dedup degrade)."""
    try:
        import redis  # type: ignore

        client = redis.Redis(decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None
