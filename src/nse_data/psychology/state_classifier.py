"""Psychological state classifier (FEATURE_CHECKLIST Week 19, tasks 19.2/19.3).

Every 5 minutes during market hours, classify each live-universe symbol into
one of 8 crowd-psychology states from already-collected data (daily bhavcopy,
the live indicator snapshot, pending events, the option chain and delivery
conviction):

    FOMO_EUPHORIA     >5 straight up days, volume building, RSI(5m)>78, >3% over VWAP
    BUY_RUMOR         +8%+ run into a result ≤5 days out with elevated IV
    SELL_NEWS         the result just landed and the pop is being sold (spike & fade)
    FEAR_BUILDING     ≥3 straight down days, RSI(5m)<40, volume building
    CAPITULATION      >4 straight down days, RSI(5m)<22, >3% under VWAP, delivery rising
    RELIEF_BOUNCE     −10%+ run into a result that resolved today, price recovering
    DEAD_CAT_BOUNCE   −8%+ 5-day fall, up today but on LESS volume than the fall
    NEUTRAL_TRENDING  none of the extremes

Precedence when several match (documented, most-specific first): the
event-anchored states (SELL_NEWS, RELIEF_BOUNCE, BUY_RUMOR) outrank the pure
momentum extremes (CAPITULATION, FOMO_EUPHORIA, DEAD_CAT_BOUNCE,
FEAR_BUILDING); NEUTRAL_TRENDING is the fallback.

The state is written to `indicator_live.psych_state` (+ the 19.1 measurement
columns) via UPSERT and mirrored into the `ind:{symbol}` Redis hash, where the
signal enrichment picks it up for the confidence scorer (Layer 7, 19.4) and
the alert line (19.5).

    run_psychology_pass(conn, redis_client)             # classify + persist
    register_state_classifier_job(scheduler, db_path)   # every 5 min
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Sequence

import structlog

from ..events.calendar import _parse_nse_date
from ..events.pre_event_risk import upcoming_result_events
from ..indicators.intraday_ohlcv import read_intraday_5m
from ..scheduler.market_hours import is_market_open, now_ist

log = structlog.get_logger()

JOB_ID = "psychology_state"
_INTERVAL_SECONDS = 300

STATES = (
    "FOMO_EUPHORIA", "BUY_RUMOR", "NEUTRAL_TRENDING", "SELL_NEWS",
    "FEAR_BUILDING", "CAPITULATION", "RELIEF_BOUNCE", "DEAD_CAT_BOUNCE",
)

# --- thresholds (checklist 19.2, named so tests/tuning reference one place) ---
_FOMO_UP_DAYS = 5            # consecutive_up_days must EXCEED this
_FOMO_RSI = 78.0
_FOMO_VWAP_PCT = 3.0         # % above session VWAP
_RUMOR_RUN_10D = 8.0
_RUMOR_DAYS_TO_EVENT = 5
_RUMOR_IV_RATIO = 1.3
_FEAR_DOWN_DAYS = 3          # consecutive_down_days at LEAST this
_FEAR_RSI = 40.0
_CAPIT_DOWN_DAYS = 4         # consecutive_down_days must EXCEED this
_CAPIT_RSI = 22.0
_CAPIT_VWAP_PCT = -3.0       # % below session VWAP
_RELIEF_RUN_10D = -10.0
_DEAD_CAT_RET_5D = -8.0

_SPIKE_WINDOW_SECS = 30 * 60     # SELL_NEWS pattern window
_SPIKE_MIN_PCT = 1.0             # pop size from window open to window high
_FADE_MIN_RETRACE = 0.6          # fraction of the pop given back

# How many daily bars feed the streak/volume reads (a >14-day streak is
# clamped — beyond any threshold above, so the clamp can't change a state).
_DAILY_LOOKBACK = 15


# ============================================================================
# Pure classifier
# ============================================================================

def classify_psych_state(m: dict) -> str:
    """One of the 8 states from a measurement dict. Missing inputs (None) fail
    the conditions that need them — except ``iv_vs_avg``, where missing option
    data doesn't veto BUY_RUMOR (consistent with 18.2, which classifies
    BUY_RUMOR_IN_PLAY from the run alone).
    """
    rsi = m.get("rsi_5m")
    vwap_pct = m.get("price_vs_vwap_pct")
    run10 = m.get("pre_event_run_10d")
    days_to_event = m.get("days_to_event")
    iv_ratio = m.get("iv_vs_avg")

    # --- event-anchored states (most specific) ---
    if m.get("event_arrived_today") and m.get("spike_and_fade"):
        return "SELL_NEWS"
    if (run10 is not None and run10 < _RELIEF_RUN_10D
            and m.get("event_arrived_today") and m.get("price_rising_today")):
        return "RELIEF_BOUNCE"
    if (run10 is not None and run10 > _RUMOR_RUN_10D
            and days_to_event is not None and days_to_event <= _RUMOR_DAYS_TO_EVENT
            and (iv_ratio is None or iv_ratio > _RUMOR_IV_RATIO)):
        return "BUY_RUMOR"

    # --- momentum extremes ---
    if (m.get("consecutive_down_days", 0) > _CAPIT_DOWN_DAYS
            and rsi is not None and rsi < _CAPIT_RSI
            and vwap_pct is not None and vwap_pct < _CAPIT_VWAP_PCT
            and m.get("delivery_rising")):
        return "CAPITULATION"
    if (m.get("consecutive_up_days", 0) > _FOMO_UP_DAYS
            and m.get("volume_rising_daily")
            and rsi is not None and rsi > _FOMO_RSI
            and vwap_pct is not None and vwap_pct > _FOMO_VWAP_PCT):
        return "FOMO_EUPHORIA"
    ret5 = m.get("ret_5d")
    vol_vs_down = m.get("today_vol_vs_down_avg")
    if (ret5 is not None and ret5 < _DEAD_CAT_RET_5D and m.get("price_rising_today")
            and vol_vs_down is not None and vol_vs_down < 1.0):
        return "DEAD_CAT_BOUNCE"
    if (m.get("consecutive_down_days", 0) >= _FEAR_DOWN_DAYS
            and rsi is not None and rsi < _FEAR_RSI
            and m.get("volume_rising_daily")):
        return "FEAR_BUILDING"
    return "NEUTRAL_TRENDING"


def consecutive_moves(closes: Sequence[float]) -> tuple[int, int]:
    """(consecutive_up_days, consecutive_down_days) from ascending closes.

    Counted from the most recent close backwards; one of the two is always 0
    (an unchanged close ends both streaks).
    """
    up = down = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            if down:
                break
            up += 1
        elif closes[i] < closes[i - 1]:
            if up:
                break
            down += 1
        else:
            break
    return up, down


def volume_rising(volumes: Sequence[float], days: int = 3) -> bool:
    """True when each of the last `days` daily volumes exceeds the one before."""
    if len(volumes) < days + 1:
        return False
    tail = volumes[-(days + 1):]
    return all(tail[i] > tail[i - 1] for i in range(1, len(tail)))


def detect_spike_and_fade(bars, now_ts: int) -> bool:
    """SPIKE_AND_FADE in the last 30 min of 5-min bars (SELL_NEWS pattern).

    A pop of ≥ ``_SPIKE_MIN_PCT``% from the window's opening price to its high,
    of which the latest close has given back ≥ ``_FADE_MIN_RETRACE`` — the
    news-pop being sold into.
    """
    if bars is None or bars.empty:
        return False
    window = bars[bars.index >= now_ts - _SPIKE_WINDOW_SECS]
    if len(window) < 2:
        return False
    base = float(window["open"].iloc[0])
    hi = float(window["high"].max())
    last = float(window["close"].iloc[-1])
    if base <= 0 or hi <= base:
        return False
    spike_pct = (hi - base) / base * 100.0
    if spike_pct < _SPIKE_MIN_PCT:
        return False
    retrace = (hi - last) / (hi - base)
    return retrace >= _FADE_MIN_RETRACE


def run_pct(closes: Sequence[float], n: int) -> float | None:
    """% move of the last close vs the close `n` bars earlier (None if short)."""
    if len(closes) < n + 1 or closes[-(n + 1)] in (None, 0):
        return None
    return round((closes[-1] - closes[-(n + 1)]) / closes[-(n + 1)] * 100.0, 2)


# ============================================================================
# Measurements (DB-coupled)
# ============================================================================

def _daily_history(conn: sqlite3.Connection, symbol: str) -> tuple[list[float], list[float]]:
    """(closes, volumes) ascending — last `_DAILY_LOOKBACK` EQ daily bars."""
    rows = conn.execute(
        "SELECT close, volume FROM raw_bhavcopy_cm "
        "WHERE symbol = ? AND series = 'EQ' ORDER BY date DESC LIMIT ?",
        (symbol, _DAILY_LOOKBACK),
    ).fetchall()
    rows.reverse()
    rows = [r for r in rows if r[0] is not None]   # keep closes/volumes aligned
    closes = [r[0] for r in rows]
    volumes = [float(r[1] or 0) for r in rows]
    return closes, volumes


def _live_row(conn: sqlite3.Connection, symbol: str) -> tuple[float | None, float | None]:
    """(vwap, rsi_5m) from indicator_live — the minute-fresh snapshot."""
    try:
        row = conn.execute(
            "SELECT vwap, rsi_5m FROM indicator_live WHERE symbol = ?", (symbol,),
        ).fetchone()
    except sqlite3.OperationalError:
        return (None, None)
    return (row[0], row[1]) if row else (None, None)


def _delivery_rising_map(conn: sqlite3.Connection) -> set[str]:
    """Symbols whose latest delivery_conviction row trends 'rising'."""
    try:
        rows = conn.execute(
            "SELECT symbol, delivery_trend, MAX(session_date) FROM delivery_conviction "
            "GROUP BY symbol",
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {r[0] for r in rows if (r[1] or "").lower() == "rising"}


def _results_filed_today(conn: sqlite3.Connection, now: _dt.datetime) -> set[str]:
    """Symbols whose result ANNOUNCEMENT broadcast today (live, unlike the
    nightly pending_events reconcile, which only flips status at 20:00)."""
    from ..fundamentals.from_results import is_result_subject

    try:
        rows = conn.execute(
            "SELECT symbol, subject, broadcast_dt FROM raw_announcements "
            "WHERE broadcast_dt IS NOT NULL ORDER BY broadcast_dt DESC LIMIT 300",
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    today = now.date()
    out: set[str] = set()
    for symbol, subject, broadcast_dt in rows:
        if not is_result_subject(subject):
            continue
        d = _parse_nse_date(broadcast_dt)
        if d == today:
            out.add(symbol)
    return out


def _iv_vs_avg(conn: sqlite3.Connection, symbol: str) -> float | None:
    """Current ATM IV vs its average over recent option-chain snapshots.

    Sampled over the last ~10 distinct snapshots so the read stays cheap; only
    computed for symbols inside the BUY_RUMOR event window. None when the
    symbol has no chain (cash-only names) or too little IV history.
    """
    try:
        as_ofs = [r[0] for r in conn.execute(
            "SELECT DISTINCT as_of FROM raw_option_chain WHERE symbol = ? "
            "ORDER BY as_of DESC LIMIT 10",
            (symbol,),
        )]
    except sqlite3.OperationalError:
        return None
    if len(as_ofs) < 3:
        return None

    def atm_iv(as_of) -> float | None:
        rows = conn.execute(
            "SELECT strike, implied_volatility, underlying_value "
            "FROM raw_option_chain WHERE symbol = ? AND as_of = ? "
            "AND implied_volatility > 0",
            (symbol, as_of),
        ).fetchall()
        spot = next((r[2] for r in rows if r[2]), None)
        if not rows or not spot:
            return None
        atm_strike = min((r[0] for r in rows), key=lambda s: abs(s - spot))
        ivs = [r[1] for r in rows if r[0] == atm_strike]
        return sum(ivs) / len(ivs) if ivs else None

    current = atm_iv(as_ofs[0])
    priors = [v for a in as_ofs[1:] if (v := atm_iv(a)) is not None]
    if current is None or not priors:
        return None
    avg = sum(priors) / len(priors)
    return round(current / avg, 2) if avg > 0 else None


def build_measurements(
    conn: sqlite3.Connection,
    symbol: str,
    now: _dt.datetime,
    *,
    events: dict[str, int],
    filed_today: set[str],
    delivery_rising_set: set[str],
) -> dict:
    """All inputs `classify_psych_state` needs for one symbol."""
    closes, volumes = _daily_history(conn, symbol)
    up, down = consecutive_moves(closes)
    vwap, rsi = _live_row(conn, symbol)

    session_open = int(now.replace(hour=9, minute=15, second=0, microsecond=0).timestamp())
    bars = read_intraday_5m(conn, symbol, since_ts=session_open)
    price = float(bars["close"].iloc[-1]) if bars is not None and not bars.empty else None

    prev_close = closes[-1] if closes else None
    price_rising_today = (price is not None and prev_close not in (None, 0)
                          and price > prev_close)

    vwap_pct = None
    if price is not None and vwap not in (None, 0):
        vwap_pct = round((price - vwap) / vwap * 100.0, 2)

    days_to_event = events.get(symbol)
    event_today = symbol in filed_today

    m = {
        "consecutive_up_days": up,
        "consecutive_down_days": down,
        "volume_rising_daily": volume_rising(volumes),
        "rsi_5m": rsi,
        "price_vs_vwap_pct": vwap_pct,
        "pre_event_run_5d": run_pct(closes, 5),
        "pre_event_run_10d": run_pct(closes, 10),
        "days_to_event": days_to_event,
        "iv_vs_avg": None,
        "event_arrived_today": event_today,
        "spike_and_fade": False,
        "delivery_rising": symbol in delivery_rising_set,
        "ret_5d": run_pct(closes, 5),
        "price_rising_today": price_rising_today,
        "today_vol_vs_down_avg": None,
    }

    # Lazy, condition-scoped reads — only pay for what could change the state.
    if event_today:
        m["spike_and_fade"] = detect_spike_and_fade(bars, int(now.timestamp()))
    if (m["pre_event_run_10d"] is not None and m["pre_event_run_10d"] > _RUMOR_RUN_10D
            and days_to_event is not None and days_to_event <= _RUMOR_DAYS_TO_EVENT):
        m["iv_vs_avg"] = _iv_vs_avg(conn, symbol)
    if (m["ret_5d"] is not None and m["ret_5d"] < _DEAD_CAT_RET_5D
            and price_rising_today):
        m["today_vol_vs_down_avg"] = _today_vol_vs_down_avg(closes, volumes, bars, now)
    return m


def _today_vol_vs_down_avg(closes, volumes, bars, now: _dt.datetime) -> float | None:
    """Today's session-fraction-scaled volume vs the avg volume of the recent
    down days (the DEAD_CAT_BOUNCE volume read: a bounce on thin volume)."""
    if bars is None or bars.empty or len(closes) < 6 or len(volumes) < 6:
        return None
    down_vols = [volumes[i] for i in range(len(closes) - 5, len(closes))
                 if closes[i] < closes[i - 1] and volumes[i] > 0]
    if not down_vols:
        return None
    down_avg = sum(down_vols) / len(down_vols)
    today_vol = float(bars["volume"].sum())
    open_ts = now.replace(hour=9, minute=15, second=0, microsecond=0)
    close_ts = now.replace(hour=15, minute=30, second=0, microsecond=0)
    elapsed = max((min(now, close_ts) - open_ts).total_seconds(), 300.0)
    fraction = min(elapsed / (close_ts - open_ts).total_seconds(), 1.0)
    projected = today_vol / fraction
    return round(projected / down_avg, 2) if down_avg > 0 else None


# ============================================================================
# Pass orchestration + persistence (19.3)
# ============================================================================

def run_psychology_pass(
    conn: sqlite3.Connection,
    *,
    redis_client=None,
    now: _dt.datetime | None = None,
    symbols: list[str] | None = None,
) -> dict:
    """Classify every live-universe symbol; write psych_state + measurements to
    indicator_live and the ind:{symbol} Redis hash. Returns per-state counts."""
    from ..indicators.universe import live_universe

    now = now or now_ist()
    symbols = symbols if symbols is not None else live_universe(conn)
    events = upcoming_result_events(conn, now.date())
    filed_today = _results_filed_today(conn, now)
    delivery_rising_set = _delivery_rising_map(conn)

    counts: dict[str, int] = {}
    rows: list[tuple] = []
    for symbol in symbols:
        try:
            m = build_measurements(
                conn, symbol, now,
                events=events, filed_today=filed_today,
                delivery_rising_set=delivery_rising_set,
            )
        except Exception:  # noqa: BLE001 — one bad symbol shouldn't kill the pass
            log.exception("psych_measurements_failed", symbol=symbol)
            continue
        state = classify_psych_state(m)
        counts[state] = counts.get(state, 0) + 1
        rows.append((symbol, now.isoformat(), state,
                     m["consecutive_up_days"], m["consecutive_down_days"],
                     m["pre_event_run_5d"], m["pre_event_run_10d"],
                     m["days_to_event"]))
        _mirror_to_redis(redis_client, symbol, state, m)

    _write_states(conn, rows)
    report = {"symbols": len(symbols), **counts}
    log.info("psychology_pass", **report)
    return report


def _write_states(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    """UPSERT psych columns onto indicator_live, preserving the live job's
    columns (same non-clobbering contract as the other indicator_live writers)."""
    if not rows:
        return
    cols = ("psych_state", "consecutive_up_days", "consecutive_down_days",
            "pre_event_run_5d", "pre_event_run_10d", "days_to_event")
    updates = ",".join(f"{c}=excluded.{c}" for c in cols)
    conn.executemany(
        f"INSERT INTO indicator_live (symbol, updated_at, {','.join(cols)}) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        f"ON CONFLICT(symbol) DO UPDATE SET {updates}",
        rows,
    )
    conn.commit()


def _mirror_to_redis(redis_client, symbol: str, state: str, m: dict) -> None:
    if redis_client is None:
        return
    values = {
        "psych_state": state,
        "consecutive_up_days": m["consecutive_up_days"],
        "consecutive_down_days": m["consecutive_down_days"],
        "pre_event_run_5d": m["pre_event_run_5d"],
        "pre_event_run_10d": m["pre_event_run_10d"],
        "days_to_event": m["days_to_event"],
    }
    try:
        redis_client.hset(
            f"ind:{symbol}",
            mapping={k: ("" if v is None else str(v)) for k, v in values.items()},
        )
    except Exception:  # noqa: BLE001 — Redis mirror is best-effort
        pass


# ============================================================================
# Scheduling (19.2: every 5 minutes, market hours only)
# ============================================================================

def run_psychology_job(db_path: str) -> dict:
    from ..storage.db import open_db

    if not is_market_open():
        return {"skipped": "market_closed"}
    conn = open_db(db_path)
    try:
        return run_psychology_pass(conn, redis_client=_connect_redis())
    finally:
        conn.close()


def register_state_classifier_job(scheduler, db_path: str) -> str:
    """Every 5 min, internally gated on market hours (cheap off-hours no-op)."""
    from apscheduler.triggers.interval import IntervalTrigger

    def _tick():
        try:
            report = run_psychology_job(db_path)
            if "skipped" not in report:
                log.info("psychology_tick", **report)
        except Exception:
            log.exception("psychology_tick_failed")

    scheduler.add_job(
        _tick, trigger=IntervalTrigger(seconds=_INTERVAL_SECONDS),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True,
    )
    return JOB_ID


def _connect_redis():
    try:
        import redis  # type: ignore

        client = redis.Redis(decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None
