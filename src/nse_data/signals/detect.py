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

    window = _time_window_skip(now)
    if window is not None:
        return {"skipped": window}

    dedup = dedup or SignalDedup(redis_client)
    detected_at = now.isoformat()
    market_regime = _market_regime(redis_client)

    symbols = live_universe(conn)
    blacklist = _load_blacklist(redis_client)
    price_bands = _load_price_bands(conn)
    listing_bars = _load_listing_bars(conn)
    quality_scores = _load_quality_scores(conn)          # task 14.4
    fresh_52w_highs = _load_today_52w_highs(conn, now)

    fired = {LONG_BUILDUP: 0, BREAKOUT_52WH: 0}
    gated = 0

    for symbol in symbols:
        series, band = price_bands.get(symbol, (None, None))
        if _hard_gated(symbol, series, listing_bars, blacklist, quality_scores):
            gated += 1
            continue

        # Both Phase-1 signals are longs, so the tight-band gate applies to both.
        band_too_tight = band is not None and band <= MAX_TIGHT_BAND_PCT

        for signal_type in (LONG_BUILDUP, BREAKOUT_52WH):
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
        "signals": fired[LONG_BUILDUP] + fired[BREAKOUT_52WH],
    }


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
) -> int:
    """Insert one row into `signals`. Returns the new signal id.

    `confidence` is left NULL here — the Week-5 scorer fills it on dispatch.
    `fake_breakout_risk` (task 15.3) trims confidence on dispatch when set.
    """
    cur = conn.execute(
        "INSERT INTO signals "
        "(symbol, signal_type, detected_at, price, oi_change_pct, "
        " price_change_pct, volume_ratio, confidence, fake_breakout_risk) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (symbol, signal_type, detected_at, price, oi_change_pct,
         price_change_pct, volume_ratio, confidence, fake_breakout_risk),
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
