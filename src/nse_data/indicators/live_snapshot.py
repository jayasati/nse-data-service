"""
Build the per-symbol `indicator_live` snapshot.

This is the "current view" the signal engine reads: one row per symbol holding
the latest VWAP + slope, ATR on two timeframes, RSI(5m), and the trend/momentum
tags. It is rebuilt every minute during market hours (folded into the live
indicator pass in `live_job.py`) and seeded once before the open by
`pre_market_loader.py`.

The build is a *read of already-computed values* plus a little local math:

    vwap, vwap_slope, price_vs_vwap   ← indicator_vwap_5m (latest session bars)
    rsi_5m                            ← indicator_rsi_5m (latest bar)
    trend_regime                      ← price vs indicator_sma (50/200)
    momentum_state                    ← rsi_5m bucketed
    atr_14_daily                      ← last ~15 raw_bhavcopy_cm candles
    atr_14_5m                         ← last ~15 five-minute bars (read-time merge)

`price` is the latest live 5-minute close when the session is running, falling
back to the last daily close (used pre-market, and when intraday data is thin).
Each field degrades to NULL independently when its inputs aren't ready yet, so a
symbol with <200 daily bars still gets VWAP/RSI even though its trend tag is
NULL. After writing SQLite we mirror each row into a Redis hash `ind:{symbol}`
with a short TTL for the signal engine's hot path (stale-detection).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Iterable

import pandas as pd

from ..scheduler.market_hours import now_ist
from .intraday_ohlcv import read_intraday_5m
from .regime import (
    classify_momentum_state,
    classify_trend_regime,
    price_vs_vwap,
    vwap_slope,
)
from .volatility.atr import atr_latest

# Columns written to indicator_live, in table order. `symbol` is the PK and
# `updated_at` is stamped at write time; the rest come from build_snapshot().
_VALUE_COLUMNS: tuple[str, ...] = (
    "vwap", "vwap_slope", "price_vs_vwap",
    "atr_14_daily", "atr_14_5m",
    "rsi_5m",
    "trend_regime", "momentum_state",
    # Full EOD set surfaced live (Week 12) — settled daily values from
    # indicator_eod, compared against the live price each minute.
    "ema9", "ema21", "bb_upper", "bb_lower", "bb_squeeze",
    "adx", "supertrend_direction", "obv",
    # Intraday 5-min set (Week 12, task 12.4) — live flip + pressure.
    "supertrend_5m_dir", "vol_delta",
    # Intraday board (live price + opening range + rvol + structure).
    "ltp", "orb_high", "orb_low", "orb_break", "rvol_5m", "structure_5m",
)

# Latest settled EOD columns pulled into the snapshot (subset of indicator_eod).
_EOD_FIELDS: tuple[str, ...] = (
    "ema9", "ema21", "bb_upper", "bb_lower", "bb_squeeze",
    "adx", "supertrend_dir", "obv",
)
_ALL_COLUMNS: tuple[str, ...] = ("symbol", "updated_at", *_VALUE_COLUMNS)

# How far back the daily ATR read reaches. ATR(14) needs 15 bars; 30 gives
# slack for the odd missing bhavcopy day without a second query.
_DAILY_LOOKBACK = 30

# Intraday read window for ATR(5m) + the live price. A 3-hour bridge always
# spans ≥15 five-minute bars once the session is ~75 minutes old (the point
# from which ATR(5m) is expected to print — see FEATURE_CHECKLIST 3.10), and
# bounds the per-symbol read so the minute pass stays cheap.
_INTRADAY_BRIDGE_SECS = 3 * 3600

# VWAP slope spans 6 bars (30 min); we pull the last 7 session points.
_VWAP_TAIL = 7

# Redis stale-detection TTL for the ind:{symbol} hashes (task 3.7).
_REDIS_TTL_SECS = 5 * 60


# ----------------------------------------------------------------- snapshot

def build_snapshot(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    now: datetime | None = None,
) -> dict:
    """Compute the indicator_live row for one symbol.

    Returns a dict keyed by `_ALL_COLUMNS`. Any field whose inputs aren't ready
    is None. `now` (IST) drives the session window; defaults to wall-clock.
    """
    now = now or now_ist()

    daily = _read_recent_daily(conn, symbol, _DAILY_LOOKBACK)
    intraday = _read_recent_intraday(conn, symbol, now)

    price = _latest_close(intraday)
    if price is None:
        price = _latest_close(daily)   # pre-market / thin intraday fallback

    sma50, sma200 = _latest_sma(conn, symbol)
    vwaps = _session_vwaps(conn, symbol, now)
    vwap = vwaps[-1] if vwaps else None
    rsi = _latest_rsi_5m(conn, symbol)
    eod = _latest_eod(conn, symbol)

    return {
        "symbol": symbol,
        "updated_at": now.isoformat(),
        "vwap": vwap,
        "vwap_slope": vwap_slope(vwaps),
        "price_vs_vwap": price_vs_vwap(price, vwap),
        "atr_14_daily": atr_latest(daily),
        "atr_14_5m": atr_latest(intraday),
        "rsi_5m": rsi,
        "trend_regime": classify_trend_regime(
            price, sma50, sma200, eod.get("ema9"), eod.get("ema21")
        ),
        "momentum_state": classify_momentum_state(rsi),
        "ema9": eod.get("ema9"),
        "ema21": eod.get("ema21"),
        "bb_upper": eod.get("bb_upper"),
        "bb_lower": eod.get("bb_lower"),
        "bb_squeeze": eod.get("bb_squeeze"),
        "adx": eod.get("adx"),
        "supertrend_direction": eod.get("supertrend_dir"),
        "obv": eod.get("obv"),
        "supertrend_5m_dir": _latest_intraday(
            conn, "indicator_supertrend_5m", "supertrend_dir", symbol),
        "vol_delta": _latest_intraday(
            conn, "indicator_volume_delta_5m", "cum_vol_delta", symbol),
        "ltp": price,
        "orb_high": _latest_intraday(conn, "indicator_orb_5m", "orb_high", symbol),
        "orb_low": _latest_intraday(conn, "indicator_orb_5m", "orb_low", symbol),
        "orb_break": _latest_intraday(conn, "indicator_orb_5m", "orb_break", symbol),
        "rvol_5m": _latest_intraday(conn, "indicator_rvol_5m", "rvol", symbol),
        "structure_5m": _latest_intraday(conn, "indicator_structure_5m", "structure", symbol),
    }


def run_snapshot_pass(
    conn: sqlite3.Connection,
    symbols: Iterable[str],
    *,
    redis_client=None,
    now: datetime | None = None,
) -> dict:
    """Build + persist indicator_live for every `symbol`. Returns a report.

    No-op (skipped) if the indicator_live table is absent — keeps callers that
    haven't migrated from crashing the rest of the indicator tick.
    """
    if not _has_table(conn, "indicator_live"):
        return {"skipped": "no_indicator_live_table"}

    now = now or now_ist()
    rows = [build_snapshot(conn, s, now=now) for s in symbols]
    write_snapshots(conn, rows)

    flushed = 0
    if redis_client is not None and rows:
        flushed = flush_to_redis(redis_client, rows)

    return {"snapshots": len(rows), "redis_flushed": flushed}


# -------------------------------------------------------------- persistence

def write_snapshots(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    """Upsert snapshot dicts into indicator_live (UPSERT on symbol).

    ON CONFLICT updates only THIS writer's columns. Other columns on the row
    (pre_event_* from the nightly risk job, psych_state / consecutive_*_days
    from the 5-min psychology pass — Weeks 18/19) belong to slower writers and
    must survive the minute rewrite, so INSERT OR REPLACE would silently wipe
    them.
    """
    rows = list(rows)
    if not rows:
        return 0
    placeholders = ",".join("?" * len(_ALL_COLUMNS))
    updates = ",".join(f"{c}=excluded.{c}" for c in _ALL_COLUMNS if c != "symbol")
    sql = (
        f"INSERT INTO indicator_live ({','.join(_ALL_COLUMNS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(symbol) DO UPDATE SET {updates}"
    )
    conn.executemany(sql, [tuple(r[c] for c in _ALL_COLUMNS) for r in rows])
    conn.commit()
    return len(rows)


def flush_to_redis(redis_client, rows: Iterable[dict], ttl: int = _REDIS_TTL_SECS) -> int:
    """Mirror each snapshot into a Redis hash `ind:{symbol}` with `ttl` seconds.

    None values are written as empty strings so the hash always reflects the
    current state (a field that went NULL doesn't keep a stale prior value).
    Best-effort: the whole flush is wrapped by the caller, so a Redis hiccup
    degrades to "SQLite-only" rather than failing the indicator tick.
    """
    rows = list(rows)
    if not rows:
        return 0
    pipe = redis_client.pipeline()
    for r in rows:
        key = f"ind:{r['symbol']}"
        mapping = {c: ("" if r[c] is None else r[c]) for c in ("updated_at", *_VALUE_COLUMNS)}
        pipe.hset(key, mapping=mapping)
        pipe.expire(key, ttl)
    pipe.execute()
    return len(rows)


# ------------------------------------------------------------------- reads

def _read_recent_daily(conn: sqlite3.Connection, symbol: str, n: int) -> pd.DataFrame:
    """Last `n` EQ daily candles for `symbol`, ascending, indexed by date."""
    rows = conn.execute(
        "SELECT date, high, low, close FROM raw_bhavcopy_cm "
        "WHERE symbol = ? AND series = 'EQ' ORDER BY date DESC LIMIT ?",
        (symbol, n),
    ).fetchall()
    df = pd.DataFrame(rows, columns=pd.Index(["date", "high", "low", "close"]))
    return df.iloc[::-1].set_index("date")   # flip to ascending


def _read_recent_intraday(
    conn: sqlite3.Connection, symbol: str, now: datetime,
) -> pd.DataFrame:
    """Recent 5-minute bars (merged history + today's live), ascending by ts."""
    since_ts = int(now.timestamp()) - _INTRADAY_BRIDGE_SECS
    return read_intraday_5m(conn, symbol, since_ts=since_ts)


def _latest_close(ohlc: pd.DataFrame) -> float | None:
    """Most recent close from an OHLC frame, or None if empty/NaN."""
    if ohlc is None or ohlc.empty or "close" not in ohlc.columns:
        return None
    value = ohlc["close"].iloc[-1]
    return None if pd.isna(value) else float(value)


def _latest_sma(conn: sqlite3.Connection, symbol: str) -> tuple[float | None, float | None]:
    """Latest (sma_50, sma_200) for `symbol`, or (None, None) if unavailable."""
    if not _has_table(conn, "indicator_sma"):
        return (None, None)
    row = conn.execute(
        "SELECT sma_50, sma_200 FROM indicator_sma WHERE symbol = ? "
        "ORDER BY date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if row is None:
        return (None, None)
    return (row[0], row[1])


def _latest_eod(conn: sqlite3.Connection, symbol: str) -> dict:
    """Latest settled indicator_eod row for `symbol` as a dict (empty if none).

    Daily-bar indicators don't change intraday, so reading the last settled row
    and comparing it against the live price each minute is the correct hybrid —
    same shape as `_latest_sma`. (Live intraday flips are the 5-min Supertrend's
    job, not the daily one.)
    """
    if not _has_table(conn, "indicator_eod"):
        return {}
    row = conn.execute(
        f"SELECT {','.join(_EOD_FIELDS)} FROM indicator_eod "
        "WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if row is None:
        return {}
    return dict(zip(_EOD_FIELDS, row))


def _latest_intraday(
    conn: sqlite3.Connection, table: str, column: str, symbol: str,
) -> float | None:
    """Latest `column` from a 5-min intraday indicator table (ts-keyed)."""
    if not _has_table(conn, table):
        return None
    row = conn.execute(
        f"SELECT {column} FROM {table} WHERE symbol = ? ORDER BY ts DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def _latest_rsi_5m(conn: sqlite3.Connection, symbol: str) -> float | None:
    if not _has_table(conn, "indicator_rsi_5m"):
        return None
    row = conn.execute(
        "SELECT rsi_14 FROM indicator_rsi_5m WHERE symbol = ? "
        "ORDER BY ts DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def _session_vwaps(
    conn: sqlite3.Connection, symbol: str, now: datetime,
) -> list[float]:
    """Today's VWAP series (ascending) from indicator_vwap_5m.

    Scoped to the current session (ts ≥ today's 09:15 IST) so the slope never
    straddles the overnight gap — VWAP resets at the open, so a cross-session
    delta would be meaningless. Returns at most the last `_VWAP_TAIL` points.
    """
    if not _has_table(conn, "indicator_vwap_5m"):
        return []
    session_start = int(
        now.replace(hour=9, minute=15, second=0, microsecond=0).timestamp()
    )
    rows = conn.execute(
        "SELECT vwap FROM indicator_vwap_5m WHERE symbol = ? AND ts >= ? "
        "ORDER BY ts DESC LIMIT ?",
        (symbol, session_start, _VWAP_TAIL),
    ).fetchall()
    return [r[0] for r in reversed(rows)]   # ascending


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None
